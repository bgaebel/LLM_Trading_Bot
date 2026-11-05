<#
.SYNOPSIS
  Builds a TwinCAT 3 solution headless via the Automation Interface (Visual Studio DTE / COM).
.DESCRIPTION
  Requires TwinCAT XAE (TcXaeShell or Visual Studio with TwinCAT integration) on this machine.
  Never activates a configuration or logs in to a runtime.
  Run in Windows PowerShell 5.1:
    powershell -NoProfile -ExecutionPolicy Bypass -STA -File scripts/build-twincat.ps1 -Solution App.sln
  Exit code: 0 = no errors in the Error List and LastBuildInfo = 0, non-zero otherwise (also on COM failure).
.NOTES
  ProgIDs per Beckhoff docs: TcXaeShell.DTE.17.0 (XAE Shell 64-bit, 4026), TcXaeShell.DTE.15.0
  (XAE Shell 4024), VisualStudio.DTE.17.0 (VS2022). List installed ones with:
    Get-ChildItem Registry::HKEY_CLASSES_ROOT | Where-Object Name -match 'DTE\.\d+\.0$'
  PLC compile errors are NOT reliably reflected in SolutionBuild.LastBuildInfo; like TcUnit-Runner,
  this script counts Error List items with ErrorLevel High.
  COM calls can fail with RPC_E_CALL_REJECTED while the shell is busy; the Retry helper covers that
  (a proper IOleMessageFilter is the robust alternative).
  Pitfall: DTE objects such as Solution and tree items are enumerable; returning them through the
  pipeline (e.g. from Retry) unrolls them to $null. Assign them directly or wrap with a leading comma.
  Status: ran against XAE 4024 (TcXaeShell.DTE.15.0) without COM errors, but a solution generated via
  Automation Interface reported LastBuildInfo=2 with an empty Error List (cause not found). Verify the
  first green build against a manual "Build Solution" in XAE before trusting this script in CI.
#>
param(
    [Parameter(Mandatory = $true)][string]$Solution,
    [string]$ProgId = "TcXaeShell.DTE.17.0"
)
$ErrorActionPreference = "Stop"

function Retry([scriptblock]$Action, [int]$Tries = 20) {
    for ($i = 1; $i -le $Tries; $i++) {
        try { return & $Action }
        catch {
            # PowerShell wraps COM errors; RPC_E_CALL_REJECTED = 0x80010001 (shell busy -> retry)
            $ex = if ($_.Exception.InnerException) { $_.Exception.InnerException } else { $_.Exception }
            if ($i -eq $Tries -or $ex.HResult -ne 0x80010001) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
}

$solutionPath = (Resolve-Path $Solution).Path
$dte = New-Object -ComObject $ProgId
$exitCode = 2
try {
    Retry { $dte.SuppressUI = $true } | Out-Null
    Retry { $dte.UserControl = $false } | Out-Null   # shell closes when automation releases it
    Retry { $dte.Solution.Open($solutionPath) } | Out-Null

    $build = Retry { $dte.Solution.SolutionBuild }
    Retry { $build.Build($true) } | Out-Null          # $true = wait for completion
    Start-Sleep -Seconds 2                             # let the Error List settle
    $failedProjects = Retry { $build.LastBuildInfo }

    # Keep the collection out of the pipeline (it would be enumerated); fetch ErrorList, then items.
    $errorList = Retry { $dte.ToolWindows.ErrorList }
    $items = $errorList.ErrorItems
    $errors = 0
    for ($i = 1; $i -le $items.Count; $i++) {
        $e = $items.Item($i)
        # EnvDTE80.vsBuildErrorLevel: 1 = Low (message), 2 = Medium (warning), 4 = High (error)
        if ($e.ErrorLevel -ge 2) {
            $kind = if ($e.ErrorLevel -eq 4) { "error" } else { "warning" }
            if ($e.ErrorLevel -eq 4) { $errors++ }
            Write-Output ("{0}({1}): {2}: {3}" -f $e.FileName, $e.Line, $kind, $e.Description)
        }
    }
    Write-Output "build-twincat: $errors error(s), LastBuildInfo = $failedProjects failed project(s)"
    $exitCode = if ($errors -gt 0 -or $failedProjects -gt 0) { 1 } else { 0 }
}
finally {
    try { $dte.Quit() } catch { }
}
exit $exitCode

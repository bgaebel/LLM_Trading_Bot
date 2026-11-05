<#
.SYNOPSIS
  Runs idf.py in a freshly activated ESP-IDF environment (one call = one shell, e.g. Claude's Bash tool).
.DESCRIPTION
  Espressif does not support Git Bash, and every tool call starts a new shell without IDF activation.
  This wrapper activates ESP-IDF itself, then runs idf.py with all arguments and returns its exit code.
  Usage (from any shell, Windows PowerShell 5.1 compatible):
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/idf.ps1 build
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/idf.ps1 -p COM5 flash
  Activation script lookup (first hit wins):
    1. $env:IDF_ACTIVATE_SCRIPT (explicit override)
    2. EIM: eim_idf.json ($env:IDF_EIM_JSON, else C:\Espressif\tools\eim_idf.json, else next to eim.exe);
       entry idfSelectedId (set via `eim select`), or the only installed version
    3. Classic Git checkout: $env:IDF_PATH\export.ps1
  If idf.py is already available (shell activated beforehand), activation is skipped.
.NOTES
  eim_idf.json layout (idfInstalled[].activationScript, idfSelectedId) per EIM docs; not verified
  against every EIM release. If it changes, set IDF_ACTIVATE_SCRIPT to the path shown by `eim select`.
#>
$ErrorActionPreference = "Stop"

function Find-ActivationScript {
    if ($env:IDF_ACTIVATE_SCRIPT) { return $env:IDF_ACTIVATE_SCRIPT }

    $candidates = @()
    if ($env:IDF_EIM_JSON) { $candidates += $env:IDF_EIM_JSON }
    $candidates += "C:\Espressif\tools\eim_idf.json"
    $eim = Get-Command eim -ErrorAction SilentlyContinue
    if ($eim) { $candidates += (Join-Path (Split-Path $eim.Source) "eim_idf.json") }
    foreach ($json in $candidates) {
        if (-not (Test-Path -LiteralPath $json)) { continue }
        $cfg = Get-Content -LiteralPath $json -Raw | ConvertFrom-Json
        $installed = @($cfg.idfInstalled)
        $sel = $installed | Where-Object { $_.id -eq $cfg.idfSelectedId } | Select-Object -First 1
        if (-not $sel -and $installed.Count -eq 1) { $sel = $installed[0] }
        if ($sel -and $sel.activationScript -and (Test-Path -LiteralPath $sel.activationScript)) {
            return $sel.activationScript
        }
    }

    if ($env:IDF_PATH) {
        $export = Join-Path $env:IDF_PATH "export.ps1"
        if (Test-Path -LiteralPath $export) { return $export }
    }
    return $null
}

if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    $activate = Find-ActivationScript
    if (-not $activate) {
        [Console]::Error.WriteLine(@"
idf.ps1: no ESP-IDF activation found.
  Install: winget install Espressif.EIM-CLI ; eim install -i v5.5.6 ; eim select v5.5.6
  Or set IDF_ACTIVATE_SCRIPT to the activation script (path shown by 'eim select'),
  or IDF_PATH to an ESP-IDF checkout containing export.ps1.
"@)
        exit 127
    }
    # Dot-source at script scope so functions defined by the activation (idf.py, ...) stay visible.
    # Banner output goes to the host, not to stdout of idf.py.
    . $activate | Out-Host
}

$ErrorActionPreference = "Continue"  # idf.py writes progress to stderr; do not treat that as failure
if (Get-Command idf.py -ErrorAction SilentlyContinue) {
    idf.py @args
} elseif ($env:IDF_PATH -and (Test-Path -LiteralPath (Join-Path $env:IDF_PATH "tools\idf.py"))) {
    python (Join-Path $env:IDF_PATH "tools\idf.py") @args
} else {
    [Console]::Error.WriteLine("idf.ps1: activation ran, but idf.py is still not available (IDF_PATH='$env:IDF_PATH').")
    exit 127
}
exit $LASTEXITCODE

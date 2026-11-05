<#
.SYNOPSIS
    Erzeugt ein neues Projekt aus diesem Template.
.DESCRIPTION
    Kopiert das Template (ohne .git und lokale Claude-Dateien) in ein neues Verzeichnis,
    setzt den Projektnamen ein, legt ein Git-Repo mit Initial-Commit an und erstellt
    optional ein privates GitHub-Repo (gh).
    Projekte gehören nicht in Sync-Ordner (Dropbox/OneDrive) - zwischen PCs per git push/pull abgleichen.
.EXAMPLE
    .\scripts\neues-projekt.ps1 -Ziel C:\Projekte\sensor-node
.EXAMPLE
    .\scripts\neues-projekt.ps1 -Ziel C:\Projekte\sensor-node -Name "Sensor Node" -GitHub
#>
# Compatible with Windows PowerShell 5.1. File must stay UTF-8 with BOM (see .editorconfig).
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Ziel,
    [string]$Name,
    [switch]$GitHub
)

$ErrorActionPreference = 'Stop'

function Fail([string]$msg) {
    Write-Host "FEHLER: $msg" -ForegroundColor Red
    exit 1
}

function Invoke-Native([string]$exe, [string[]]$arguments) {
    # PS 5.1 turns native stderr (git/gh progress) into terminating errors under 'Stop' when output is redirected.
    $ErrorActionPreference = 'Continue'
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "'$exe $($arguments -join ' ')' fehlgeschlagen (Exit $LASTEXITCODE)." }
}

Add-Type -Namespace NeuesProjekt -Name Native -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern Microsoft.Win32.SafeHandles.SafeFileHandle CreateFileW(string lpFileName, uint dwDesiredAccess,
    uint dwShareMode, IntPtr lpSecurityAttributes, uint dwCreationDisposition, uint dwFlagsAndAttributes, IntPtr hTemplateFile);
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern uint GetFinalPathNameByHandleW(Microsoft.Win32.SafeHandles.SafeFileHandle hFile,
    System.Text.StringBuilder lpszFilePath, uint cchFilePath, uint dwFlags);
'@

# Canonical form of an existing path: resolves 8.3 short names, junctions and symlinks.
function Get-FinalPath([string]$path) {
    $full = [System.IO.Path]::GetFullPath((Get-Item -LiteralPath $path -Force).FullName)
    # Share read/write/delete, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS (required for directories).
    $h = [NeuesProjekt.Native]::CreateFileW($full, 0, 7, [IntPtr]::Zero, 3, 0x02000000, [IntPtr]::Zero)
    if ($h.IsInvalid) { return $full.TrimEnd('\') }
    try {
        $sb = New-Object System.Text.StringBuilder 32768
        $len = [NeuesProjekt.Native]::GetFinalPathNameByHandleW($h, $sb, $sb.Capacity, 0)
        if ($len -eq 0 -or $len -ge $sb.Capacity) { return $full.TrimEnd('\') }
        $p = $sb.ToString()
        if ($p.StartsWith('\\?\UNC\')) { $p = '\\' + $p.Substring(8) }
        elseif ($p.StartsWith('\\?\')) { $p = $p.Substring(4) }
        return $p.TrimEnd('\')
    }
    finally { $h.Dispose() }
}

# Canonical form of a possibly non-existing path: canonicalize the deepest existing ancestor.
function Get-CanonicalPath([string]$path) {
    $full = [System.IO.Path]::GetFullPath($path).TrimEnd('\')
    $rest = @()
    $cur = $full
    while ($cur -and -not (Test-Path -LiteralPath $cur)) {
        $rest = @(Split-Path $cur -Leaf) + $rest
        $cur = Split-Path $cur -Parent
    }
    if (-not $cur) { return $full }
    $result = Get-FinalPath $cur
    foreach ($r in $rest) { $result = $result + '\' + $r }
    return $result
}

$templateRoot = Get-CanonicalPath (Join-Path $PSScriptRoot '..')
# Resolve relative to the PowerShell location ([IO.Path]::GetFullPath alone would use the process directory).
$target = Get-CanonicalPath ($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Ziel))
if (-not $Name) { $Name = Split-Path $target -Leaf }

# --- Checks ---
if (($target + '\').StartsWith($templateRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    Fail "Ziel liegt im Template-Ordner: $target"
}
if (Test-Path -LiteralPath $target) {
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { Fail "Ziel ist eine Datei: $target" }
    if (Get-ChildItem -LiteralPath $target -Force | Select-Object -First 1) { Fail "Ziel existiert und ist nicht leer: $target" }
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git nicht gefunden." }
if ($GitHub) {
    $gh = (Get-Command gh -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if (-not $gh) {
        $winGh = Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe'
        if (Test-Path -LiteralPath $winGh) { $gh = $winGh }
    }
    if (-not $gh) { Fail "gh (GitHub CLI) nicht gefunden. Installation: winget install GitHub.cli" }
    # PS 5.1 turns redirected native stderr into errors under 'Stop'; relax temporarily.
    $ErrorActionPreference = 'Continue'
    & $gh auth status *> $null
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -ne 0) { Fail "gh ist nicht angemeldet. Erst 'gh auth login' ausführen." }
}

# --- Copy ---
# Relative paths (backslash form) excluded from copying; directories exclude everything below them.
$excluded = @('.git', '.claude\settings.local.json', '.claude\worktrees', 'CLAUDE.local.md')

function Test-Excluded([string]$rel) {
    foreach ($e in $excluded) {
        if ($rel -ieq $e -or $rel.StartsWith($e + '\', [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

# Collect the complete file list before creating/copying anything (never walks into its own output).
$sourceRoot = (Get-Item -LiteralPath $templateRoot -Force).FullName.TrimEnd('\')
$files = @(Get-ChildItem -LiteralPath $sourceRoot -Recurse -Force -File |
    Where-Object { -not (Test-Excluded $_.FullName.Substring($sourceRoot.Length + 1)) })

New-Item -ItemType Directory -Path $target -Force | Out-Null
foreach ($file in $files) {
    $dest = Join-Path $target $file.FullName.Substring($sourceRoot.Length + 1)
    $destDir = Split-Path $dest -Parent
    if (-not (Test-Path -LiteralPath $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
    Copy-Item -LiteralPath $file.FullName -Destination $dest -Force
}
Write-Host "$($files.Count) Dateien kopiert nach $target"

# --- Project name placeholder ---
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
foreach ($f in @('CLAUDE.md', 'docs\PROJEKT.md')) {
    $p = Join-Path $target $f
    if (Test-Path -LiteralPath $p) {
        $text = [System.IO.File]::ReadAllText($p, $utf8NoBom)
        [System.IO.File]::WriteAllText($p, $text.Replace('{{PROJEKTNAME}}', $Name), $utf8NoBom)
    }
}

# --- Git ---
Push-Location $target
try {
    Invoke-Native git @('init', '-q', '-b', 'main')
    Invoke-Native git @('-c', 'core.safecrlf=false', 'add', '-A')
    # Windows has no exec bit: mark shell scripts executable in the index for Linux/macOS clones.
    $ErrorActionPreference = 'Continue'
    $shFiles = @(& git ls-files -- 'scripts/*.sh' '.claude/hooks/*.sh')
    $ErrorActionPreference = 'Stop'
    if ($shFiles.Count -gt 0) { Invoke-Native git (@('update-index', '--chmod=+x', '--') + $shFiles) }
    Invoke-Native git @('commit', '-q', '-m', 'chore: initialize project from template')
    Write-Host "Git-Repo angelegt (Branch main, Initial-Commit)."

    if ($GitHub) {
        # GitHub repo names: only A-Z a-z 0-9 . _ -
        $repo = $Name.Replace('ä', 'ae').Replace('ö', 'oe').Replace('ü', 'ue').Replace('Ä', 'Ae').Replace('Ö', 'Oe').Replace('Ü', 'Ue').Replace('ß', 'ss')
        $repo = ($repo -replace '[^A-Za-z0-9._-]+', '-').Trim('-')
        if (-not $repo) { Fail "Kein gültiger Repo-Name aus '$Name' ableitbar." }
        Invoke-Native $gh @('repo', 'create', $repo, '--private', '--source', $target, '--push')
        Write-Host "GitHub-Repo '$repo' (privat) erstellt und gepusht."
    }
}
finally {
    Pop-Location
}

# --- Sync folder warning ---
if ($target -match '\\(Dropbox|OneDrive)[^\\]*(\\|$)') {
    Write-Host ""
    Write-Host "WARNUNG: Das Projekt liegt in einem Sync-Ordner ($($Matches[1]))." -ForegroundColor Yellow
    Write-Host "  Empfehlung: Projekte AUSSERHALB von Sync-Ordnern ablegen (z. B. C:\Projekte\...) und"
    Write-Host "  zwischen PCs nur per git push/pull abgleichen. Grund: Sync-Dienst und Git sind zwei"
    Write-Host "  konkurrierende Sync-Wege - halb synchronisierte .git-Ordner gehen kaputt, und"
    Write-Host "  maschinenspezifische Ordner (.venv, node_modules, .pio, build) gehören nicht auf andere PCs."
    Write-Host "  Wenn es doch hier bleiben muss: den GANZEN Projektordner von Dropbox ausnehmen (nicht nur .git):"
    Write-Host "    Set-Content -Path '$target' -Stream com.dropbox.ignored -Value 1"
    Write-Host "  OneDrive kennt kein Ignore pro Ordner - dort das Projekt unbedingt verschieben."
}

Write-Host ""
Write-Host "Nächste Schritte:" -ForegroundColor Green
Write-Host "  cd `"$target`""
Write-Host "  claude"
Write-Host "  /kickoff"

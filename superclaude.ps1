# superclaude.ps1 — Claude Code launcher for Windows
# Delegates to bin/superclaude.ps1 via WSL if available, else native.

$UU_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$BinLauncher = Join-Path $UU_ROOT "bin\superclaude.ps1"
$PyLauncher = Join-Path $UU_ROOT "superclaude"

# Try WSL first (preferred — full bash environment)
if (Get-Command wsl -ErrorAction SilentlyContinue) {
    $WslBin = (wsl wslpath -u ($UU_ROOT.Replace('\', '/') + "/bin/superclaude")) 2>$null
    if ($WslBin) {
        wsl bash $WslBin @args
        exit $LASTEXITCODE
    }
}

# Native PowerShell fallback via bin/superclaude.ps1
if (Test-Path $BinLauncher) {
    & $BinLauncher @args
    exit $LASTEXITCODE
}

# Last resort: Python launcher
$Python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command python -ErrorAction SilentlyContinue }
if ($Python) {
    & $Python.Source $PyLauncher @args
    exit $LASTEXITCODE
}

Write-Error "superclaude: no launcher found. Ensure WSL or Python 3.10+ is installed."
exit 1

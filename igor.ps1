# igor.ps1 — Igor launcher for Windows
# Delegates to bin/superclaude.ps1 via WSL if available, else native Python.

$UU_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$IgorPy = Join-Path $UU_ROOT "igor"

# Try WSL first
if (Get-Command wsl -ErrorAction SilentlyContinue) {
    $WslPath = (wsl wslpath -u $IgorPy.Replace('\', '/')) 2>$null
    if ($WslPath) {
        wsl python3 $WslPath @args
        exit $LASTEXITCODE
    }
}

# Native Python fallback
$Python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command python -ErrorAction SilentlyContinue }
if ($Python) {
    & $Python.Source $IgorPy @args
    exit $LASTEXITCODE
}

Write-Error "igor: python3 not found. Install Python 3.10+ and retry."
exit 1

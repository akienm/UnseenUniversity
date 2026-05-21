# adc.ps1 — ADC management CLI (Windows entry point)
# Bootstraps the venv if needed, then delegates to agentctl.
#
# Usage:
#   .\bin\adc.ps1 init [--instance <name>]
#   .\bin\adc.ps1 skills deploy
#   .\bin\adc.ps1 status
#
# Set $env:IGOR_HOME and $env:THEIGORS_HOME before calling if non-default.

$ErrorActionPreference = 'Stop'

$adcRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition | Split-Path -Parent

# Set env vars expected by skills scripts (mirrors superclaude.ps1)
if (-not $env:IGOR_HOME)     { $env:IGOR_HOME     = "$env:USERPROFILE\.TheIgors" }
if (-not $env:THEIGORS_HOME) {
    $localRoot = Split-Path -Parent $adcRoot
    $env:THEIGORS_HOME = "$localRoot\TheIgors"
}
if (-not $env:PYTHONUTF8) { $env:PYTHONUTF8 = '1' }

$adcScript = Join-Path $adcRoot "bin\adc"
& python $adcScript @args

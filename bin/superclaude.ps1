# superclaude.ps1 -- Claude Code launcher for Windows (mirror of Linux `superclaude`).
#
# What it does:
#   1. Loads per-instance .env (so ANTHROPIC_API_KEY etc. flow into CC)
#   2. Ensures the utility closet is running in the background
#   3. Launches `claude` with --dangerously-skip-permissions, forwarding all args
#
# Windows differences vs Linux superclaude:
#   - No tmux (Windows has no equivalent; self-compaction via tmux send-keys is Linux-only)
#   - UC auto-start is via a Scheduled Task (install with install_uc_autostart.ps1);
#     this script still ensures UC is up as a fallback
#
# Usage:
#   .\superclaude.ps1                      # start Claude Code
#   superclaude                            # if repo root is in PATH (via superclaude.bat)

$ErrorActionPreference = 'Stop'

$repoRoot    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$adcRoot     = Split-Path -Parent $repoRoot               # agent_datacenter root
$localRoot   = Split-Path -Parent $adcRoot                # C:\automation\local (or equivalent)
$runtimeRoot = "$env:USERPROFILE\.TheIgors"
$instanceId  = if ($env:IGOR_INSTANCE_ID) { $env:IGOR_INSTANCE_ID } else { 'Igor-wild-0001' }
$envFile     = "$runtimeRoot\$instanceId\.env"

# ---- Set platform env vars used by skills scripts ----
if (-not $env:IGOR_HOME)      { $env:IGOR_HOME      = $runtimeRoot }
if (-not $env:THEIGORS_HOME)  { $env:THEIGORS_HOME  = "$localRoot\TheIgors" }
if (-not $env:PYTHONUTF8)     { $env:PYTHONUTF8     = '1' }

# ---- Load .env if present (KEY=value lines; ignores comments and blanks) ----
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { return }
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim().Trim('"').Trim("'")
        Set-Item -Path "env:$key" -Value $val
    }
}

# ---- Ensure logs dir ----
New-Item -ItemType Directory -Force -Path "$runtimeRoot\logs" | Out-Null

# ---- Ensure utility closet is running ----
& "$repoRoot\start_utility_closet.ps1"

# ---- Launch Claude Code ----
$env:PYTHONUTF8 = '1'

# Prefer REAL_ANTHROPIC_API_KEY if set (CC uses real Anthropic; Igor routes via OR).
# This is the Windows analogue of the Linux cc.sh key-swap.
if ($env:REAL_ANTHROPIC_API_KEY) {
    $env:ANTHROPIC_API_KEY = $env:REAL_ANTHROPIC_API_KEY
}

$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) {
    Write-Host "ERROR: 'claude' not found on PATH" -ForegroundColor Red
    Write-Host "Install Claude Code CLI or add it to PATH, then re-run." -ForegroundColor Yellow
    exit 1
}

& claude --dangerously-skip-permissions @args

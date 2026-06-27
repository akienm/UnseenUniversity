# superclaude.ps1 (root) — thin pass-through to the canonical launcher in bin/.
#
# NO logic lives here. All behavior is in bin/superclaude.ps1. This shim exists
# only so the repo-root path still works; every argument, including --help, is
# forwarded untouched so the bin/ launcher's own flags are authoritative.
# (T-launcher-help-and-1m-flags)
& (Join-Path $PSScriptRoot "bin\superclaude.ps1") @args
exit $LASTEXITCODE

@echo off
rem superclaude.bat — entry point: type "superclaude" to launch Claude Code on Windows.
rem Add the TheIgors repo root to your PATH to use from anywhere.
rem
rem Unlike the earlier version, this does NOT re-elevate — it just hands off to
rem superclaude.ps1 in the current shell. Run from an elevated shell if you want
rem elevated Claude Code (which is what --dangerously-skip-permissions expects).

powershell.exe -ExecutionPolicy Bypass -File "%~dp0superclaude.ps1" %*

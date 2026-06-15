# D-unified-daemon-supervisor-2026-06-15

**title:** Unified daemon supervisor via Ground Loop — cron bootstrap + file placement pattern

**date:** 2026-06-15

**status:** open

**spawned_tickets:** T-groundloop-cron-bootstrap, T-daemon-supervisor-file-pattern, T-daemon-supervisor-demo, T-consequence-unified-daemon-supervisor

## Decision narrative

Consolidate daemon management under a single Ground Loop supervisor. Cross-platform compatible: system cron (Linux, macOS) triggers Ground Loop startup; Ground Loop runs as a persistent loop (optionally with delay gates for Windows). All device daemons are `devices/*/groundloop/runme.py` modules — drop a file, Ground Loop hot-reloads it. Config in YAML/JSON alongside (launch frequency, etc.). Error handling: if a daemon crashes, Ground Loop renames it to `.borkedpy` to exclude it from reload. On next startup or manual fix, rename back to `.py` to re-enable. Simplicity: "make a file, put it in the right place. That's it."

**Cross-platform strategy:**
- Linux/macOS: system cron invokes `ground_loop_start.sh` → Ground Loop runs, loads all runme.py, serves daemons forever
- Windows: Ground Loop runs as a service or startup task, loads all runme.py, serves daemons forever
- Ground Loop itself is cross-platform (Python + no Unix-specific APIs)

## Hypothesis

**Observable difference:** From this point forward, to create a new daemon, write `devices/mydevice/groundloop/runme.py` and place it. Ground Loop auto-discovers and runs it. System is simpler to maintain and extend.

**Signal:** New daemons created via file placement only; no hand-coded dispatcher or supervisor logic needed; system resilience improves (failed daemon doesn't kill others; Ground Loop recovers).

**Goal link:** G-self-improving-system, G-simplification, G-resilience

## Open questions (none — design confirmed)

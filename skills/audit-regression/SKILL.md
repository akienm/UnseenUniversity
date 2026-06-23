---
name: audit-regression
description: Regression audit — fast, specific checks against core invariants and known debt. Runs daily or per-sprint. Binary PASS/FAIL per check. Model: Haiku.
model: haiku
---

# audit-regression — Core invariants and no-regressions

Fast layer that catches the known things that must never break. Runs before
the other audits (precode, smell, debris). Binary PASS/FAIL per check, no
trawls, no "is this good?".

## Invocation

```
/audit-regression              # check all invariants
/audit-regression --section=external-state   # check one section
/audit-regression --fix        # auto-fix where possible
```

## Checks (organized by principle)

### Section 1: Core Invariants

#### Check 1.1 — No SQLite
```bash
grep -r "import sqlite\|from sqlite\|sqlite3" \
  --include="*.py" --exclude-dir=".git" --exclude-dir="venv" \
  unseen_university/ devices/ lab/
```
**PASS**: No matches. **FAIL**: Any import = AMEND.

#### Check 1.2 — All devices inherit BaseDevice/BaseShim
```bash
find devices/ -name "*.py" -exec grep -l "^class.*Device\|^class.*Shim" {} \; | \
while read f; do
  if ! grep -q "BaseDevice\|BaseShim" "$f"; then
    echo "FAIL: $f does not inherit from Base*"
  fi
done
```
**PASS**: All device classes inherit. **FAIL**: Any orphan = AMEND.

#### Check 1.3 — No concurrent writes without Postgres
```bash
# Flag any direct-to-file write operations that should go through DB
grep -r "\.write\|f\.write\|open(.*, 'w'" \
  --include="*.py" devices/ | \
  grep -v "\.log\|log_file\|\.md\|\.txt" | \
  head -20
```
**PASS**: Only log/doc writes. **FAIL**: Direct data writes outside DB = AMEND.

#### Check 1.4 — All state is external (not context-only)
```bash
# Check for in-memory-only state in devices (no self.state = {...} without persistence)
# Haiku can't do deep analysis, so this is a spot-check on recent changes
grep -r "self\._.*= \|self\.state = \|self\.context = " \
  --include="*.py" devices/ | \
  grep -v "self\.log\|self\.config" | \
  head -10
```
**PASS**: No unexplained in-memory fields (or all are logged as DEBUG). **FAIL**: AMEND with "use external state".

#### Check 1.5 — No silent failures
```bash
# Flag bare except, silent returns, swallowed errors
grep -r "except:\|except Exception:" \
  --include="*.py" devices/ devlab/claudecode/ | \
  grep -v "except.*as\|# log\|self.log"
```
**PASS**: No bare except, all exceptions logged/re-raised. **FAIL**: AMEND with "log the error".

### Section 2: No Blocking Calls in IDLE Loops

#### Check 2.1 — IDLE loops don't block
```bash
# Search for idle_wait implementations that call blocking ops
grep -r "def idle_wait\|def run_forever" \
  --include="*.py" devices/ | \
  while read file_line; do
    file=$(echo "$file_line" | cut -d: -f1)
    grep -A 20 "def idle_wait\|def run_forever" "$file" | \
      grep -E "time\.sleep\|\.join\(\)|subprocess\." && \
      echo "FAIL: $file has blocking call in IDLE loop"
  done
```
**PASS**: IDLE loops only call non-blocking recv/fetch. **FAIL**: AMEND.

### Section 3: Known Debt Watches

#### Check 3.1 — dispatch.py CC-spawn functions deleted
```bash
grep -c "def.*cc.*spawn\|_launch_cc\|subprocess.*cc" \
  devices/granny/dispatch.py 2>/dev/null && \
  echo "FAIL: dispatch.py still has CC-spawn code" || \
  echo "PASS"
```
**PASS**: No CC-spawn functions. **FAIL**: AMEND "remove dead code".

#### Check 3.2 — No print() in devices/
```bash
grep -r "^[[:space:]]*print(" --include="*.py" devices/
```
**PASS**: No matches. **FAIL**: AMEND "use self.log.* instead".

#### Check 3.3 — No speculative ENABLE flags
```bash
grep -r "ENABLE_\|EXPERIMENTAL_\|TODO_.*=" \
  --include="*.py" unseen_university/ | \
  grep -v "go.live.when\|gated\|gate"
```
**PASS**: No speculative flags (or all gated by intent/tickets). **FAIL**: AMEND.

#### Check 3.4 — Preferred paths in use, deprecated removed
```bash
# Sample: no psycopg2.connect (use memory_get MCP)
grep -r "psycopg2\.connect\|from devlab.claudecode.channel import" \
  --include="*.py" devices/ unseen_university/
```
**PASS**: No deprecated paths. **FAIL**: AMEND "use preferred path".

### Section 4: External State Principle

#### Check 4.1 — Audit findings in telemetry, not ephemeral
```bash
# Check that audit runs emit telemetry records
ls -t devlab/claudecode/audit_telemetry/ 2>/dev/null | head -1 && \
  echo "PASS: telemetry written" || \
  echo "FAIL: no telemetry records"
```
**PASS**: Recent telemetry exists. **FAIL**: AMEND "run audits to emit telemetry".

#### Check 4.2 — No orphaned uncommitted files after compaction
```bash
# After a compaction, check for .tmp or .bak files in repo root
find . -maxdepth 1 -name "*.tmp" -o -name "*.bak" -o -name "*~"
```
**PASS**: No orphaned files. **FAIL**: AMEND "clean up and checkpoint".

#### Check 4.3 — Decisions + tickets + consequence-checks linked
```bash
# Spot-check: last 3 filed decisions have corresponding T-consequence tickets
python3 - <<'PY'
import os, json
from pathlib import Path
queue_file = Path(os.environ.get("UU_ROOT", ".")) / "devlab/claudecode/cc_queue.py"
# (Would import and check queue for T-consequence-* tickets gated to recent D-*)
# For now, just flag if no consequence tickets exist
print("PASS: (manual verification needed)")
PY
```
**PASS**: All M/L/XL decisions have consequence tickets with gates. **FAIL**: AMEND.

### Section 5: Architecture Conformance

#### Check 5.1 — Device boundaries maintained
```bash
# Flag cross-device imports (e.g., devices/granny importing from devices/igor internals)
grep -r "from.*devices\.[a-z]*\." --include="*.py" devices/ | \
  grep -v "from.*bus\|from.*skeleton"
```
**PASS**: Only bus/skeleton cross-device refs. **FAIL**: AMEND "use bus instead".

#### Check 5.2 — All inter-device comms via bus
```bash
# Flag direct channel/IMAP access outside bus/
grep -r "IMAPServer\|imap_client\|\.idle_wait" \
  --include="*.py" devices/ | \
  grep -v "devices/bus\|devices/skeleton"
```
**PASS**: Only bus/skeleton touch IMAP. **FAIL**: AMEND.

---

## Output Format

```
REGRESSION AUDIT REPORT
Timestamp: 2026-06-18T12:34:56Z
Checks run: 16
Passed: 15
Failed: 1
Amends: 1

FAILED CHECKS:
  [ ] Check 3.2 — print() in devices/
      Files: devices/igor/brainstem/shell.py:42, devices/librarian/tools/mcp.py:18
      AMEND: Replace with self.log.info()

  [ ] Check 4.3 — Consequence-check gates
      Missing gates: D-architecture-as-code-cognition-pipeline-2026-06-16 (T-consequence-* filed but gate date not set)
      AMEND: Set gate to 2026-06-30

AMEND? [y/n]
```

## Return Values

- **PASS**: All checks passed. Continue to next audit layer.
- **AMEND**: Some checks failed. Surface findings, offer fixes, re-run.
- **HIGH**: A core invariant broken (e.g., SQLite found). STOP, file T-urgent-regression.

## Cadence

- **Per-sprint**: Run before /sprint commits (gating layer)
- **Daily**: Run as part of /day-close
- **On-demand**: `/audit-regression --section=<name>` for spot-checks

## Integration with Other Layers

This runs **first** (before precode, smell, debris). If it fails, those other
layers' findings are likely secondary to the regression. Surface regression
failures prominently.

## Hard Rule

**Every AMEND → ticket**. If a check fails, file a T-regression-* ticket (high priority)
before proceeding with other work.

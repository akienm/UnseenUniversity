---
name: day-close-audit
description: Debris-and-hygiene check for UU, run during /day-close. MANDATORY part of day-close — never skip. Checks for debris (temp files, leaked runtime state, dead code), tests, file placement, code smells, registry coherence, inertia check, thread hygiene, log sizes, OR burn rate, DB schema, duplication, habit health, TWM coverage, dependency hygiene, credential scan, and simplification review. Fix small issues now, ticket anything bigger.
model: haiku
model_exception: Step 17 (simplification review) requires Sonnet — escalate that step inline.
---

# Day-Close Audit — Automated Debris & Health Check

⛔ **MANDATORY for day-close. This is not optional. If skipped, day-close is incomplete.**

Produces a findings report. Fix small issues now (missing log call, bare except, typo).
Ticket anything medium/large. After fixes: /commit, then continue day-close.

---

## Step 1 — Tests

```bash
cd ~/dev/src/UnseenUniversity && source .venv/bin/activate && python -m pytest tests/ -x -q 2>&1 | tail -20
```

If tests fail: **STOP**. Fix before proceeding. Offer to run `/test-fix`.

---

## Step 2 — File placement

```bash
python3 ${CC_WORKFLOW_TOOLS}/validate_files.py 2>/dev/null | head -30
```

Note any misplaced files. Small fixes now; large restructures → ticket.

---

## Step 3 — Code smell scan

```bash
cd ~/dev/src/UnseenUniversity && source .venv/bin/activate && python3 - << 'EOF'
import ast, pathlib

issues = []
src = pathlib.Path("wild_igor/igor")
for f in sorted(src.rglob("*.py")):
    try:
        tree = ast.parse(f.read_text())
    except SyntaxError as e:
        issues.append(f"SYNTAX_ERROR|{f}|{e}")
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                issues.append(f"BARE_EXCEPT_PASS|{f}:{node.lineno}")
        if isinstance(node, ast.ExceptHandler):
            if all(isinstance(s, ast.Pass) for s in node.body):
                issues.append(f"SILENT_EXCEPT|{f}:{node.lineno}")

for i in issues:
    print(i)
print(f"\n{len(issues)} smell(s) found")
EOF
```

For each finding: is there a log call in the except block? If not → add one now.

---

## Step 4 — Registry coherence

```bash
cd ~/dev/src/UnseenUniversity && source .venv/bin/activate && python3 - << 'EOF'
import sys, os
os.environ.setdefault("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")
)
sys.path.insert(0, ".")
from wild_igor.igor.tools.registry import registry
import wild_igor.igor.tools  # noqa

tools = registry._tools.values()
print(f"Registered tools: {len(list(tools))}")
for t in sorted(registry._tools.values(), key=lambda x: x.name):
    print(f"  {t.name}")
EOF
```

Check: registered tools whose `fn` no longer exists? Tool functions in files NOT registered?

---

## Step 5 — Inertia check

```bash
cd ~/dev/src/UnseenUniversity && git log --oneline --name-only $(git log --format=%H --grep='audit' -1 2>/dev/null || git rev-list --max-parents=0 HEAD)..HEAD \
  | grep -E "brainstem/|memory/models\.py|cognition/reasoners/base\.py" | sort -u
```

HIGH-inertia files without a corresponding Dxxx decision → findings gap.

---

## Step 6 — Thread hygiene

```bash
grep -rn "ThreadPoolExecutor" ~/dev/src/UnseenUniversity/devices/igor/ 2>/dev/null || echo "None found — OK"
```

Verify each usage has daemon=True or uses a queue pattern.

---

## Step 7 — Log file sizes

```bash
du -sh ~/.unseen_university/logs/*.log 2>/dev/null | sort -rh | head -10
```

Any file > 10MB → rotate automatically:
```bash
python3 ~/dev/src/UnseenUniversity/lab/claudecode/rotate_logs.py
```

`rotate_logs.py` renames files >10MB to `.log.1` (one backup kept) and recreates an empty log.
Safe to call on every day-close — silent when all logs are under 10MB.

---

## Step 8 — OR burn rate

```bash
cd ~/dev/src/UnseenUniversity && source .venv/bin/activate && python3 - << 'EOF'
import os, sys
sys.path.insert(0, ".")
os.environ.setdefault("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")
from wild_igor.igor.tools.budget import get_balance_trajectory
print(get_balance_trajectory(window_hours=48))
EOF
```

`burning_fast` (>$20/day) or days_remaining < 3 → surface to Akien immediately.

---

## Step 9 — DB schema spot-check

```bash
cd ~/dev/src/UnseenUniversity && source .venv/bin/activate && python3 - << 'EOF'
import os
os.environ.setdefault("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")
import psycopg2
conn = psycopg2.connect(os.environ["IGOR_HOME_DB_URL"])
cur = conn.cursor()
cur.execute("""
    SELECT table_schema || '.' || table_name AS qname
    FROM information_schema.tables
    WHERE table_schema IN ('clan', 'instance', 'public')
""")
tables = {r[0] for r in cur.fetchall()}
# Post-D-sqlite-removal: canonical schemas are clan.* (cross-instance) and
# instance.* (per-instance). public.* is legacy/system only.
required = {"clan.memories", "clan.interpretive_edges", "instance.ring_memory", "instance.twm_observations"}
missing = required - tables
print(f"MISSING: {missing}" if missing else f"Schema OK — {len(tables)} tables across clan/instance/public")
conn.close()
EOF
```

---

## Step 10 — Dead code / orphan detection

```bash
cd ~/dev/src/UnseenUniversity && python3 - << 'EOF'
import pathlib, re

src = pathlib.Path("wild_igor/igor")
all_py = list(src.rglob("*.py"))

# Find .py files that are never imported by anything else in the tree
all_text = "\n".join(f.read_text(errors="ignore") for f in all_py)
orphans = []
for f in all_py:
    mod = f.stem
    if mod in ("__init__", "conftest"):
        continue
    # Check if this module name appears in any import statement
    pattern = rf"\b{re.escape(mod)}\b"
    if not re.search(pattern, all_text):
        orphans.append(str(f))

if orphans:
    print(f"{len(orphans)} possible orphan modules (not imported anywhere):")
    for o in orphans:
        print(f"  {o}")
else:
    print("No orphan modules found — OK")
EOF
```

Flag files that nothing imports — candidates for removal (discuss with Akien first).

---

## Step 11 — Duplication scan

```bash
cd ~/dev/src/UnseenUniversity && python3 - << 'EOF'
import pathlib, ast, hashlib, collections

src = pathlib.Path("wild_igor/igor")
# Collect function bodies as normalized source blocks (>10 lines)
blocks = collections.defaultdict(list)
for f in src.rglob("*.py"):
    try:
        tree = ast.parse(f.read_text())
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines = ast.unparse(node).splitlines()
            if len(lines) > 10:
                # Normalize: strip name, just body
                body = "\n".join(lines[1:])
                h = hashlib.md5(body.encode()).hexdigest()[:8]
                blocks[h].append(f"{f}:{node.name}:{node.lineno}")

dupes = {h: locs for h, locs in blocks.items() if len(locs) > 1}
if dupes:
    print(f"{len(dupes)} near-duplicate function bodies (>10 lines):")
    for h, locs in dupes.items():
        print(f"  [{h}]")
        for l in locs:
            print(f"    {l}")
else:
    print("No duplicate function bodies found — OK")
EOF
```

Duplicates → candidates for shared primitive. Flag; ticket if worth abstracting.

---

## Step 12 — Habit health

NOTE: code_ref dispatch resolves `code_ref.split(":")[-1]` → tool registry lookup.
Check against registry, NOT source text (string-search produces false positives).

```bash
cd ~/dev/src/UnseenUniversity && source .venv/bin/activate && python3 - << 'EOF'
import os, sys, json
sys.path.insert(0, ".")
os.environ.setdefault("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")
)

from wild_igor.igor.tools.registry import registry
import wild_igor.igor.tools  # loads all tools
registered = set(registry._tools.keys())

import psycopg2
conn = psycopg2.connect(os.environ["IGOR_HOME_DB_URL"])
cur = conn.cursor()
cur.execute("""
    SELECT id, metadata->>'code_ref'
    FROM clan.memories
    WHERE memory_type='PROCEDURAL'
      AND jsonb_exists(metadata, 'code_ref')
""")
rows = cur.fetchall()
conn.close()

dead = []
for id_, code_ref in rows:
    if not code_ref:
        continue
    fn_name = code_ref.split(":")[-1] if ":" in code_ref else code_ref
    if fn_name not in registered:
        dead.append(f"  DEAD code_ref: {id_} → {code_ref}  [fn={fn_name}]")

if dead:
    print(f"{len(dead)} habits reference dead code:")
    print("\n".join(dead))
else:
    print(f"Habit health OK — {len(rows)} code_ref habits checked")
EOF
```

Dead `code_ref` habits → update or remove from DB.

---

## Step 13 — TWM push coverage

```bash
grep -rn "twm_push\|TWM_PUSH\|PRIM_TWM_PUSH" ~/dev/src/UnseenUniversity/devices/igor/ 2>/dev/null | grep -v "__pycache__" | grep -v "test_"
echo "---"
# Flag significant cognitive events missing twm_push:
grep -rn "def _run_turn\|habit_fired\|ne_cycle\|consolidation_pass\|memory_deposit" \
    ~/dev/src/UnseenUniversity/devices/igor/ --include="*.py" -l 2>/dev/null
```

Review: do significant state changes (habit fire, NE completion, memory deposit) push to TWM?
If not → flag as TWM coverage gap (invisible to cognition).

---

## Step 14 — Dependency hygiene

```bash
cd ~/dev/src/UnseenUniversity && python3 - << 'EOF'
import pathlib, re, ast

# Packages declared in requirements.txt
req_file = pathlib.Path("wild_igor/requirements.txt")
declared = set()
if req_file.exists():
    for line in req_file.read_text().splitlines():
        line = line.strip().split("==")[0].split(">=")[0].split("~=")[0].lower()
        if line and not line.startswith("#"):
            declared.add(line.replace("-", "_"))

# Top-level imports actually used in source
used = set()
for f in pathlib.Path("wild_igor/igor").rglob("*.py"):
    try:
        tree = ast.parse(f.read_text())
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                used.add(alias.name.split(".")[0].lower())
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                used.add(node.module.split(".")[0].lower())

# Third-party = used but not stdlib (rough heuristic: not in declared and not single-word stdlib)
import sys
stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()
unused_declared = declared - used - stdlib
undeclared_used = used - declared - stdlib - {"wild_igor", "igor", "__future__"}

if unused_declared:
    print(f"Declared but not imported ({len(unused_declared)}): {unused_declared}")
if undeclared_used:
    # Filter to likely third-party (rough)
    likely_third_party = {m for m in undeclared_used if m not in stdlib}
    if likely_third_party:
        print(f"Possibly undeclared deps ({len(likely_third_party)}): {likely_third_party}")
if not unused_declared and not undeclared_used:
    print("Dependency hygiene OK")
EOF
```

---

## Step 15 — Credential / hardcoded path scan

```bash
cd ~/dev/src/UnseenUniversity && grep -rn \
    -e "choose_a_password" \
    -e "api_key\s*=\s*['\"][a-zA-Z0-9_-]\{20,\}" \
    -e "password\s*=\s*['\"][^'\"]\{8,\}" \
    -e "Igor-wild-0001" \
    --include="*.py" wild_igor/igor/ 2>/dev/null | grep -v "__pycache__" | grep -v "test_" | grep -v "\.pyc"
```

Hardcoded instance names → should use `paths().instance_id` or env var.
Hardcoded credentials → must move to `.env`.

---

## Step 16 — POC / TODO scan

Scan for partial implementations missing follow-up tickets:

```bash
cd ~/dev/src/UnseenUniversity && grep -rn "# POC:\|# TODO:\|# LIMITATION:\|# HACK:" wild_igor/ lab/tools/ lab/claudecode/ --include="*.py" | grep -v __pycache__ | head -30
```

For each hit: verify there's a matching ticket in cc_queue. If not, flag it.

Also scan for code that handles only the simple case without flagging the gap — common pattern:
- A function that processes `item` but not `list[item]`
- A parser that handles format A but silently drops format B
- A loop that breaks after first match when it should collect all

Add unflagged POCs to findings report. Ticket any that could cause wasted effort.

---

## Step 17 — Simplification review

For each file modified since the last audit, ask:
- Is there more complexity here than the problem requires?
- Is there a standard architectural pattern (registry, queue, channel, observer) that would replace bespoke logic?
- Is there a class or function that exists only to serve one caller? Could it be inlined?
- Are there >3 similar blocks that should be one abstraction?

```bash
cd ~/dev/src/UnseenUniversity && git diff --name-only $(git log --format=%H -1 --grep="audit" 2>/dev/null || git rev-list --max-parents=0 HEAD)..HEAD \
  | grep "\.py$" | grep "wild_igor/"
```

Read each changed file briefly. Add simplification candidates to findings report.
This step requires judgment — it cannot be fully automated.

---

## Step 18 — Registered audit checks

Run any checks registered via `audit_add.py`. These are checks added at the moment of insight (either one-shot for the next sweep, or persistent for all future sweeps). The seed forever checks include:
- `no-sqlite-imports` — CLAUDE.md hard rule against SQLite
- `no-bare-except-pass` — silent error swallow detector
- `primary-classes-must-inherit-igorbase` — D125 enforcement

```bash
cd ~/dev/src/UnseenUniversity && python3 lab/claudecode/audit_runner.py --drain 2>&1
```

The `--drain` flag moves any `next_sweep` entries to history after running so they don't repeat. Add findings to the report alongside the static-step findings. Severity: HIGH = fix or ticket immediately, MED = ticket if not trivial, LOW = note in findings.

To register a new check during normal work:
```bash
python3 lab/claudecode/audit_add.py add forever "name" --kind grep --pattern "REGEX" --description "why" --severity high
python3 lab/claudecode/audit_add.py add next "name" --kind shell --pattern "command" --severity med
python3 lab/claudecode/audit_add.py list   # show all registered
python3 lab/claudecode/audit_add.py rm "name"
python3 lab/claudecode/audit_add.py ack "name" --until 2026-04-30   # silence false positive
```

Kinds: `grep` (regex across wild_igor/), `sql` (psql against home DB), `shell` (one-liner; non-empty stdout = fail), `python` (inline expression; truthy = fail).

---

## Step 18.5 — Wiring check (gated feature verification)

Verify that enabled switches (IGOR_*=true in igor.switches.cfg) have end-to-end wiring — no stubs, no placeholders, no NotImplementedError in the gated code path. Born from two incidents (2026-04-16b) where flipping switches without verifying output caused Igor to become incoherent and then crash.

```bash
cd ~/dev/src/UnseenUniversity && python3 lab/claudecode/wiring_check.py
```

Exit code 0 = all OK. Any UNREFERENCED or STUB_NEAR_GATE findings → ticket or fix before the switch stays enabled.

**Hard rule:** Never enable a gated feature without running this check first.

---

## Step 18.6 — Capability map drift check

`~/dev/src/UnseenUniversity/docs/capability_map.md` is the "what's built today vs planned vs broken" doc. It rots fast. When it's >7 days old, the audit always re-verifies §1 (live), §2 (gated off), and §4 (known broken) against:
- Palace `unseenuniversity/subsystem_index/*` for live subsystems
- `~/.unseen_university/Igor-wild-0001/igor.switches.cfg` for gate state
- `cc_queue.py list` for in_progress / pending / awaiting_approval status
- Latest `pytest` summary for known failures

```bash
AGE_DAYS=$(( ( $(date +%s) - $(stat -c %Y ~/dev/src/UnseenUniversity/docs/capability_map.md 2>/dev/null || date +%s) ) / 86400 ))
echo "capability_map.md age: ${AGE_DAYS} days"
if [ "$AGE_DAYS" -gt 7 ]; then
  echo "⚠ capability_map.md is stale — re-verify §1, §2, §4 claims and update Last-updated date."
else
  echo "capability_map.md fresh — drift check skipped."
fi
```

Drift findings → fix the doc inline (small) or ticket the reorg work (large).

---

## Step 18.7 — Completion audit (verify closed tickets were actually built)

Check that recently-closed tickets' completion criteria are met in the actual repo.
This is the verification half of the ticket-quality loop — catches fake completions.

```bash
python3 lab/claudecode/completion_audit.py list --days 1
```

For each ticket returned with criteria (the "Auditable" section):

1. Read the **Completion criteria** field.
2. For each verifiable criterion (file exists, function present, grep match): check it with Bash.
   - `grep -r "pattern" path` — content/code checks
   - `ls path` or `cat path | grep "text"` — file existence/content
3. For behavioral criteria ("runs and shows X in transcript") — verdict: `cannot-verify`.
4. Record each verdict:

```bash
python3 lab/claudecode/completion_audit.py log-result <ticket-id> <pass|fail|cannot-verify> "<one-line reason>"
```

**Findings:**
- `fail` → **HIGH severity** finding: "T-xxx completion criteria not met: <reason>". Draft a re-open ticket.
- `pass` → note inline, no action needed.
- `cannot-verify` → note inline, no action needed (behavioral criteria require live observation).

After all tickets checked, print summary:

```bash
python3 lab/claudecode/completion_audit.py summary --days 1
```

Add to the findings report: `Completion audit: N pass, N fail, N cannot-verify`

---

## Step 19 — Evaluate findings + fix

For each finding across Steps 1–18.6:
- **Small fix** (missing log, silent except, typo, dead import): fix now
- **Medium/large** (architecture issue, missing test, inertia violation, duplication worth abstracting): ticket it

After fixes: run `/commit` with message `fix: post-audit small fixes — <date>`.

---

## Findings report format

```
AUDIT — YYYY-MM-DD
Tests:           PASS (N/N) | FAIL (<details>)
Files:           OK | <N> misplaced
Code smells:     <N> issues
Registry:        <N> tools, <N> unregistered
Inertia:         OK | HIGH files without decision: <list>
Threads:         OK | <N> to verify
Logs:            OK | <file> over 10MB
Burn rate:       $X/day (<trend>) — <N>d remaining
Schema:          OK | MISSING: <tables>
Dead code:       OK | <N> orphan modules
Duplication:     OK | <N> duplicate bodies
Habit health:    OK | <N> dead code_refs
TWM coverage:    OK | <N> uncovered events
Dependencies:    OK | unused: <list> | undeclared: <list>
Credentials:     OK | <N> hardcoded
Simplification:  <N> candidates — <brief list>
Wiring:          OK | <N> switches with stubs/missing refs
Cap-map drift:   fresh | stale (<N>d old — re-verify §1/§2/§4)
Completion audit: <N> pass, <N> fail, <N> cannot-verify | FAIL: T-xxx — <reason>

Fixed now:  <list>
Ticketed:   <list>
```

---

## Hard rules

- **Day-close audit is mandatory; it's the day-close integrity gate — every day-close runs it.**
- Audit surfaces candidates for deletion; removal happens after Akien review (deletion lives outside audit).
- Small issues get fixed inline during audit; medium/large issues get ticketed.
- Step 1 (tests) runs before anything else — a failing test blocks the rest.
- Simplification review (Step 17) requires actual judgment — "no changes found" is earned by looking, not default.
- After fixes, /commit runs before the day-close continues.

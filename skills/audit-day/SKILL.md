---
name: audit-day
description: Day-close audit — inherits all day-close-audit checks and adds cross-day watch-for, fix-one-leave-many sweep, subsystem_index vs reality, inertia tag drift, TWM coverage gaps, habit health, and auto-draft scan-for-rest tickets. Single expert lens (Process/Meta Engineer). Runs during /day-close step 4. Emits telemetry. Model: Sonnet.
model: sonnet
---

# audit-day — Day-close hygiene + cross-day watch

`/day-close-audit` covers the static checks (tests, smells, registry, etc.).
`audit-day` adds the temporal layer: what changed across today's commits, what
watch-for notes hit, and what the fix-one-leave-many sweep found.

## Invocation

Called by `/day-close` step 4. Also callable directly: `/audit-day`.

## Steps

### Step 1 — Static checks (inherit day-close-audit)

Run all 18 steps from `/day-close-audit`. Those findings feed into this
report alongside the cross-day findings.

```bash
# Already runs during /day-close — output carried forward
```

### Step 2 — Today's diffs (cross-file consistency)

```bash
git log --since="24 hours ago" --oneline
git diff HEAD~$(git log --since="24 hours ago" --oneline | wc -l)..HEAD --stat
```

For each changed file, check: did related callers also change?

**Fix-one-leave-many sweep:**
```bash
# For each function signature change in today's diffs,
# find all call sites and verify they were updated
git diff HEAD~N..HEAD | grep "^-def \|^-    def " | while read sig; do
  func=$(echo "$sig" | sed 's/^-.*def //' | sed 's/(.*$//')
  echo "--- callers of $func ---"
  grep -rn "$func(" wild_igor/igor/ lab/ --include="*.py" | grep -v "def $func"
done 2>/dev/null | head -40
```

When callers exist that weren't in today's diff: flag as fix-one-leave-many.
Auto-draft a scan-for-rest ticket (see Step 8).

### Step 3 — Watch-for notes from prior runs

```bash
python3 -c "
from devlab.claudecode.audit_telemetry import read_watch_next
notes = read_watch_next('day', include_expired=False)
for n in notes:
    print(n['path'])
    print(n['content'][:200])
    print()
"
```

For each active watch-for note:
- Did it hit in today's commits? → record as hit in telemetry; mark `hit: true`.
- Not hit, still within TTL → carry forward (age counter +1).
- Past TTL → mark expired.

### Step 4 — Subsystem_index vs reality

```
memory_get(path="unseenuniversity/subsystem_index")
```

For each entry (area → primary file), verify the primary file still exists
and is still the load-bearing file for that area. Rename or deletion = flag.

### Step 5 — Inertia tag drift

```bash
# HIGH-inertia files that changed today without a documented inertia check
git diff HEAD~N..HEAD --name-only | grep -E "brainstem/|memory/models|reasoners/base" | head -10
```

Each HIGH-inertia file changed today: verify the commit message or ticket body
has a pre-approval stamp. Missing stamp = flag.

### Step 6 — TWM coverage gaps

```bash
grep -rn "def _run_turn\|habit_fired\|ne_cycle\|consolidation_pass" \
    wild_igor/igor/ --include="*.py" | grep -v "__pycache__" | head -20
```

Significant state changes (habit fire, NE completion, memory deposit) that
don't call `cortex.twm_push()` = flag as TWM coverage gap.

### Step 7 — Habit health

```bash
python3 -c "
import os, sys
sys.path.insert(0, '.')
os.environ.setdefault('UU_HOME_DB_URL', 'postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001')
from wild_igor.igor.tools.registry import registry
import wild_igor.igor.tools
import psycopg2
conn = psycopg2.connect(os.environ['UU_HOME_DB_URL'])
cur = conn.cursor()
cur.execute(\"SELECT id, metadata->>'code_ref' FROM memories WHERE memory_type='PROCEDURAL' AND jsonb_exists(metadata, 'code_ref')\")
rows = cur.fetchall()
conn.close()
registered = set(registry._tools.keys())
dead = [(id_, cr) for id_, cr in rows if cr and cr.split(':')[-1] not in registered]
print(f'{len(dead)} dead code_refs / {len(rows)} total')
for id_, cr in dead[:5]:
    print(f'  {id_}: {cr}')
"
```

Dead `code_ref` habits → update or remove. More than 5 = HIGH.

### Step 8 — Fix-one-leave-many: auto-draft scan-for-rest tickets

When Step 2 found partial call-graph updates, draft a ticket:
```bash
python3 ${CC_WORKFLOW_TOOLS}/scan_for_rest_drafter.py \
  --function <func_name> \
  --found-callers <file1,file2> \
  --missing-callers <file3,file4> \
  --output /tmp/scan-for-rest-<func>.json
```

The JSON lands in `/tmp/` — never auto-files. Surfaces in next `/decided` session.

### Step 9 — Emit telemetry

```python
from devlab.claudecode.audit_telemetry import emit_run_record, emit_watch_next, AuditRunRecord

record = AuditRunRecord(
    level="day",
    checks_fired=N,
    checks_passed=M,
    # ... populate from findings above
    model="sonnet",
)
emit_run_record("day", record)

# For new patterns worth watching tomorrow:
emit_watch_next("day", "Watch for partial signature changes in <area>", ttl_days=3)
```

## Output format

```
AUDIT-DAY — YYYY-MM-DD
Static checks:    PASS | N findings (from day-close-audit steps 1-18)
Today's diffs:    N commits, M files changed
Fix-one-leave-many: <N> partial updates found | OK
Watch-for hits:   <N> hit | <M> aged | <P> expired
Subsystem drift:  OK | <N> renamed primaries
Inertia tags:     OK | <N> undocumented HIGH-inertia changes
TWM gaps:         OK | <N> significant events without twm_push
Habit health:     OK | <N> dead code_refs
Scan-for-rest:    <N> tickets drafted to /tmp/

Fixed now:  <list>
Ticketed:   <list>
```

## Expert lens

Single lens: **Process / Meta Engineer**. Ask:
- Is the build process getting faster or slower?
- Are the audit checks catching real bugs or generating noise?
- Is the test suite growing in proportion to the code?
- Are habits and schedules still aligned with current architecture?

Do NOT run the full 11-expert panel (`/deep-audit`). That's a weekly/monthly
operation.

## Hard rules

- Always runs during `/day-close` step 4 — this IS the audit step, not an
  optional add-on.
- Fix-one-leave-many sweep always runs — it's the primary value-add over
  static day-close-audit.
- Watch-for notes always age or hit — never silently skip.
- Emit telemetry even on PASS runs — trend data requires uniform sampling.

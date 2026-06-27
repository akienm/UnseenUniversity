---
name: eval-run
description: Weekly capability snapshot — 5 behavioral questions about what the system can actually do, independent of ticket velocity. Run Fridays as part of day-close, or standalone. Output to devlab/runtime/memory/notes/eval-run.YYYYMMDD.md.
model: sonnet
---

# /eval-run — Weekly capability snapshot

> **Status: partially deferred.** Several eval questions targeted TheIgors cognition internals (clan.memories, instance.ring_memory, pe_chain) that have no UnseenUniversity analog yet. Those are marked deferred below; the rest read the flat-file store / logs.

Tests verify code correctness. Evals verify capability.

The distinction matters: 4624 tests passing doesn't tell you whether Igor can
take a ticket from sprint to committed code without human intervention. Evals
ask that question directly.

Run Fridays. Takes ~5 minutes. Five questions, observable data, one screen.

## Invocation

```
/eval-run              — run the standard weekly eval set
```

---

## The 5 standard evals

These answer the core capability questions for the current goals. Update them
when active goals change.

---

### Eval 1 — pe_chain end-to-end success rate

**Question:** Did Igor complete at least one pe_chain ticket from sprint to committed code this week, without SCOPE_GUARD block or old_string mismatch?

**Data source:**
```bash
# (Deferred: needs UU cognition substrate — no analog yet.)
```

**Eval result:** `deferred`

---

### Eval 2 — NE stuck rate

**Question:** Is Igor's NE stuck cycle rate trending down week-over-week?

**Data source:**
```bash
# (Deferred: needs UU cognition substrate — no analog yet.)
```

**Eval result:** `deferred`

---

### Eval 3 — Dreaming pipeline active

**Question:** Did the dreaming pipeline produce non-test proposals this week?

**Data source:**
```bash
# (Deferred: needs UU cognition substrate — no analog yet.)
```

**Eval result:** `deferred`

---

### Eval 4 — Done:closed gap

**Question:** Is the gap between awaiting_validation (done) and closed tickets narrowing or widening?

**Data source:**
```bash
python3 - << 'EOF'
import os, pathlib, json, collections
UU_ROOT = pathlib.Path(os.environ.get("UU_ROOT", str(pathlib.Path.home() / "dev/src/UnseenUniversity")))
tickets_dir = UU_ROOT / "devlab/runtime/memory/tickets"
counts = collections.Counter()
if tickets_dir.exists():
    for f in tickets_dir.rglob("*.json"):
        try:
            data = json.loads(f.read_text())
            status = data.get("status", "unknown")
            counts[status] += 1
        except Exception:
            pass
for status, count in sorted(counts.items(), key=lambda x: -x[1]):
    print(f"{status}: {count}")
EOF
```

Compare to last week's count (use prior eval note from `devlab/runtime/memory/notes/` if available).

**Report as:** `Done:closed gap: N awaiting_validation vs M closed | delta vs last week: +N/-N | trend: narrowing/widening/flat`

---

### Eval 5 — Autonomous operation

**Question:** Did any action this week require unexpected human intervention that a well-functioning system shouldn't need?

**Data source:**
```bash
# CC inbox entries from this week
python3 -c "
from devlab.claudecode.cc_inbox import read_unread
import datetime
entries = [e for e in read_unread() if True]  # all entries
print(f'{len(entries)} inbox entries')
for e in entries[:10]:
    print(f'  [{e.urgency}] {e.kind}: {e.summary}')
"

# Channel escalations
grep -rh "SCOPE_GUARD\|BLOCKED\|stuck\|escalat" \
  ~/.unseen_university/logs/*/info/*.json 2>/dev/null | \
  grep "$(date -d '7 days ago' +%Y-%m-%d)\|$(date +%Y-%m-%d)" | \
  grep -v "test\|TEST" | wc -l
```

**Report as:** `Autonomy: N unexpected interventions | notable: <list or "none"> | trend: improving/flat/worsening`

---

## Steps

### 1. Run all 5 evals

Run each eval's data query, compute the answer, note trend direction.

### 2. Write results to notes

```python
import os, pathlib, datetime
datestamp = datetime.datetime.now().strftime("%Y%m%d")
UU_ROOT = pathlib.Path(os.environ.get("UU_ROOT", str(pathlib.Path.home() / "dev/src/UnseenUniversity")))
output_path = UU_ROOT / f"devlab/runtime/memory/notes/eval-run.{datestamp}.md"
# Write eval results to devlab/runtime/memory/notes/
# e.g.: output_path.write_text(report_text)
```

### 3. Report

```
/eval-run — YYYY-MM-DD
pe_chain:  N/M succeeded (X%) | trend: ↑/→/↓
NE stuck:  N events | avg valence: X.XX | trend: ↑/→/↓
Dreaming:  N proposals | status: active/cold-start
Done:closed gap: N awaiting vs M closed | delta: +N/-N
Autonomy:  N interventions | trend: ↑/→/↓
```

---

## Updating the standard evals

The 5 evals answer: "what can the system actually do right now?" Update them
when capability claims change, not when intentions change.

---

## Hard rules

- Evals answer capability questions; they do not re-run unit tests.
- Trend direction is required for every eval — raw numbers without direction are half the answer.
- If a data source is unavailable (log format changed, table missing): mark as `UNAVAILABLE` and /note why.

---
name: eval-run
description: Weekly capability snapshot — 5 behavioral questions about what Igor can actually do, independent of ticket velocity. Run Fridays as part of day-close, or standalone. Output to palace.evals.YYYYMMDD.
model: sonnet
---

# /eval-run — Weekly capability snapshot

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
# From flight recorder logs
grep -h "pe_chain\|HYPOTHESIZE\|commit_result" \
  ~/.unseen_university/$IGOR_INSTANCE_ID/logs/*.log 2>/dev/null | \
  grep "$(date -d '7 days ago' +%Y-%m-%d)\|$(date +%Y-%m-%d)" | \
  tail -100

# From cc_queue: tickets closed by Igor this week
psql "$UU_HOME_DB_URL" -tAc \
  "SELECT id, metadata->>'title', metadata->>'closed_by'
   FROM clan.memories
   WHERE parent_id='TICKETS_ROOT'
     AND metadata->>'status' IN ('done','closed','awaiting_validation')
     AND updated_at > now() - interval '7 days'"
```

**Report as:** `pe_chain: N tickets attempted, M succeeded (X%) | trend: up/flat/down`

---

### Eval 2 — NE stuck rate

**Question:** Is Igor's NE stuck cycle rate trending down week-over-week?

**Data source:**
```bash
# Channel messages about stuck NE
grep "\[NE\].*stuck" ~/.unseen_university/$IGOR_INSTANCE_ID/logs/*.log 2>/dev/null | \
  awk -F'T' '{print $1}' | sort | uniq -c | tail -14

# Psych log valence trend
psql "$UU_HOME_DB_URL" -tAc \
  "SELECT date_trunc('day', to_timestamp(ts)) as day,
          AVG(valence) as avg_v, COUNT(*) as cycles
   FROM instance.ring_memory
   WHERE ts > extract(epoch from now() - interval '14 days')
   GROUP BY 1 ORDER BY 1"
```

**Report as:** `NE stuck: N events this week vs M last week | avg valence: X.XX | trend: improving/flat/worsening`

---

### Eval 3 — Dreaming pipeline active

**Question:** Did the dreaming pipeline produce non-test proposals this week?

**Data source:**
```bash
psql "$UU_HOME_DB_URL" -tAc \
  "SELECT source_module, kind, COUNT(*), MAX(created_at)
   FROM instance.proposals
   WHERE source_module != 'test'
     AND created_at > now() - interval '7 days'
   GROUP BY 1, 2 ORDER BY 3 DESC"
```

**Report as:** `Dreaming: N proposals this week (source_module=dreaming) | pipeline: active/cold-start/silent`

---

### Eval 4 — Done:closed gap

**Question:** Is the gap between awaiting_validation (done) and closed tickets narrowing or widening?

**Data source:**
```bash
psql "$UU_HOME_DB_URL" -tAc \
  "SELECT metadata->>'status', COUNT(*) FROM clan.memories
   WHERE parent_id='TICKETS_ROOT' GROUP BY 1 ORDER BY 2 DESC"
```

Compare to last week's count (use palace.evals.* from prior week if available).

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
grep -h "SCOPE_GUARD\|BLOCKED\|stuck\|escalat" \
  ~/.unseen_university/$IGOR_INSTANCE_ID/logs/*.log 2>/dev/null | \
  grep "$(date -d '7 days ago' +%Y-%m-%d)\|$(date +%Y-%m-%d)" | \
  grep -v "test\|TEST" | wc -l
```

**Report as:** `Autonomy: N unexpected interventions | notable: <list or "none"> | trend: improving/flat/worsening`

---

## Steps

### 1. Run all 5 evals

Run each eval's data query, compute the answer, note trend direction.

### 2. Write to palace

```python
datestamp = datetime.now().strftime("%Y%m%d")
path = f"palace.evals.{datestamp}"
# Write eval results to adc.palace
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

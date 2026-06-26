---
name: weekly-retro
description: 5-minute Friday retrospective — reviews hypothesis confirmation rate and what changes about next week's priorities. Called automatically by day-close on Fridays. Also callable standalone. Output to palace.retro.YYYYMMDD.
model: sonnet
---

# /weekly-retro — Friday hypothesis review

Day-close covers code health. Expert audit covers discipline health.
Weekly-retro covers the question neither asks: **are we making the right bets?**

5 minutes. Three questions. One screen of output.

## When to run

- Automatically: triggered by day-close when `date +%u` = 5 (Friday)
- Manually: `/weekly-retro` any time

---

## Steps

### 1. Pull this week's decision outcomes

```bash
psql "$UU_HOME_DB_URL" -tAc \
  "SELECT path, title, metadata->>'outcome', metadata->>'outcome_date'
   FROM adc.palace
   WHERE path LIKE 'palace.decisions.%'
     AND metadata->>'outcome_date' > (now() - interval '7 days')::date::text
   ORDER BY metadata->>'outcome_date' DESC"
```

Also list decisions that closed this week but have no outcome yet:
```bash
psql "$UU_HOME_DB_URL" -tAc \
  "SELECT path, title FROM adc.palace
   WHERE path LIKE 'palace.decisions.%'
     AND metadata->>'outcome' IS NULL
     AND metadata->>'status' = 'open'
     AND updated_at > now() - interval '7 days'"
```

### 2. Answer the two questions

**Q1 — Hypothesis confirmation rate this week**
Count outcomes: confirmed + partially_confirmed vs. falsified + inconclusive.
If >50% falsified or inconclusive: flag — we may be designing against wrong assumptions.
If no outcomes recorded: flag — the outcome loop isn't closing.

**Q2 — What changes about next week?**
Based on Q1: should any priorities shift? Any decisions that look wrong in light of this week's outcomes? Any intentions that should be revisited?
This is the one synthesis question. It doesn't require a long answer — one sentence is enough.

### 3. Surface unreviewed hypotheses

List decisions that have shipped (all tickets closed) but /outcome hasn't been run:
```
Needs /outcome: D-xxx (shipped N days ago), D-yyy (shipped M days ago)
```
Flag any that are >14 days overdue.

### 4. Write to palace

```python
import psycopg2, psycopg2.extras
from datetime import datetime, timezone

datestamp = datetime.now().strftime("%Y%m%d")
content = f"""## Week ending {datetime.now().strftime('%Y-%m-%d')}

### Hypothesis confirmation rate
{q1_summary}

### Priority changes for next week
{q2_summary}

### Needs /outcome
{overdue_outcomes or 'none'}
"""
# INSERT INTO adc.palace (path='palace.retro.{datestamp}', node_type='retro', ...)
```

### 5. Report

```
/weekly-retro — week ending YYYY-MM-DD
Outcomes this week: N confirmed, M falsified, P too_early, Q pending
Next week: <priority changes>
Needs /outcome: <list or "none">
```

---

## Hard rules

- Always run even if there are no outcomes this week — a zero-outcome week is itself a signal.
- If no outcomes were recorded in 2+ weeks, surface that directly: the outcome loop has stopped closing.
- Q3 (what changes) requires at least one sentence — "nothing changes" is an acceptable answer but must be stated.

---
name: weekly-retro
description: 5-minute Friday retrospective — reviews hypothesis confirmation rate, goal KR trends, and what changes about next week's priorities. Called automatically by day-close on Fridays. Also callable standalone. Output to palace.retro.YYYYMMDD.
model: sonnet
---

# /weekly-retro — Friday hypothesis + goal review

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

### 2. Pull goal KR snapshots

```bash
psql "$UU_HOME_DB_URL" -tAc \
  "SELECT path, title, metadata->>'key_results', metadata->>'last_kr_update'
   FROM adc.palace
   WHERE path LIKE 'palace.goals.%'
     AND metadata->>'status' = 'active'
   ORDER BY updated_at DESC"
```

### 3. Answer the three questions

**Q1 — Hypothesis confirmation rate this week**
Count outcomes: confirmed + partially_confirmed vs. falsified + inconclusive.
If >50% falsified or inconclusive: flag — we may be designing against wrong assumptions.
If no outcomes recorded: flag — the outcome loop isn't closing.

**Q2 — Goal KR trends**
For each active goal: is the KR moving in the right direction, flat, or moving wrong?
Use the most recent /outcome verdicts and /eval-run data as evidence.
Name any goal where KR progress is stalled for >2 weeks.

**Q3 — What changes about next week?**
Based on Q1 + Q2: should any priorities shift? Any decisions that look wrong in light of this week's outcomes? Any goals that should be retired or blocked?
This is the one synthesis question. It doesn't require a long answer — one sentence per goal is enough.

### 4. Surface unreviewed hypotheses

List decisions that have shipped (all tickets closed) but /outcome hasn't been run:
```
Needs /outcome: D-xxx (shipped N days ago), D-yyy (shipped M days ago)
```
Flag any that are >14 days overdue.

### 5. Write to palace

```python
import psycopg2, psycopg2.extras
from datetime import datetime, timezone

datestamp = datetime.now().strftime("%Y%m%d")
content = f"""## Week ending {datetime.now().strftime('%Y-%m-%d')}

### Hypothesis confirmation rate
{q1_summary}

### Goal KR trends
{q2_summary}

### Priority changes for next week
{q3_summary}

### Needs /outcome
{overdue_outcomes or 'none'}
"""
# INSERT INTO adc.palace (path='palace.retro.{datestamp}', node_type='retro', ...)
```

### 6. Report

```
/weekly-retro — week ending YYYY-MM-DD
Outcomes this week: N confirmed, M falsified, P too_early, Q pending
Goal KR trends: <one line per active goal>
Next week: <priority changes>
Needs /outcome: <list or "none">
```

---

## Hard rules

- Always run even if there are no outcomes this week — a zero-outcome week is itself a signal.
- If no outcomes were recorded in 2+ weeks, surface that directly: the outcome loop has stopped closing.
- Q3 (what changes) requires at least one sentence — "nothing changes" is an acceptable answer but must be stated.

---
name: outcome
description: Review a decision's hypothesis against observable evidence and record whether it confirmed, falsified, or needs more time. Triggered at last-ticket close or manually. Updates the goal's KR progress. This is what closes the learning loop.
model: sonnet
---

# /outcome — Hypothesis review at decision close

The learning loop has no value unless it closes. /outcome is the closing step —
it takes the hypothesis stated when the decision was filed and asks: did it hold?

Without /outcome, we ship indefinitely and accumulate done tickets without
knowing if the system is actually improving.

## When to run

- **Automatically prompted**: when the last ticket of a decision moves to closed/awaiting_validation, /sprint-ticket surfaces the prompt: "Last ticket of D-xxx just closed — run /outcome?"
- **Manually**: `/outcome D-xxx` at any time after the decision's work has had time to show results.
- **Weekly retro**: /weekly-retro surfaces un-reviewed decision hypotheses as a standing item.

## Invocation

```
/outcome D-xxx             — review hypothesis for a specific decision
/outcome                   — list decisions with unreviewed hypotheses
```

---

## Steps

### 1. Read the hypothesis

```bash
DECISION_FILE=~/TheIgors/lab/design_docs/decisions/<D-id>.md
grep -A 20 "## Hypothesis" "$DECISION_FILE"
```

If the decision predates hypothesis tracking (no `## Hypothesis` section), note that and skip to a general outcome assessment.

### 2. Gather observable evidence

Based on the measurement signal stated at /decided time, collect current data. Common patterns:

```bash
# NE cycle health
psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT date_trunc('day', created_at), COUNT(*) as cycles,
          AVG((metadata->>'valence')::float) as avg_valence
   FROM clan.memories WHERE memory_type='NE_CYCLE'
     AND created_at > now() - interval '14 days'
   GROUP BY 1 ORDER BY 1"

# pe_chain success rate (from flight recorder logs)
grep "HYPOTHESIZE" ~/.unseen_university/Igor-wild-0001/logs/*.log 2>/dev/null | \
  grep -c "success\|error" | head -20

# proposals accumulation
psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT source_module, COUNT(*) FROM instance.proposals
   WHERE source_module != 'test' GROUP BY 1"

# done:closed ratio trend
psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT metadata->>'status', COUNT(*) FROM clan.memories
   WHERE parent_id='TICKETS_ROOT' GROUP BY 1 ORDER BY 2 DESC"
```

Use whatever data source the hypothesis named. If no specific source was named, use the most relevant observable proxy.

### 3. Assess the verdict

Compare the hypothesis claim against the evidence. Choose one:

- **confirmed** — evidence clearly supports the claim. KR moved in the right direction.
- **partially_confirmed** — some evidence supports it; other aspects didn't move or are unclear.
- **falsified** — evidence contradicts the claim. The expected change didn't happen.
- **too_early** — insufficient time has passed or data accumulated to tell. Set a re-check date.
- **inconclusive** — the measurement signal named at hypothesis time wasn't available or was ambiguous.

### 4. Write verdict to decision record

Append to the decision's `.md` file:

```bash
cat >> ~/TheIgors/lab/design_docs/decisions/<D-id>.md << EOF

## Outcome — $(date +%Y-%m-%d)
**Verdict:** <confirmed / partially_confirmed / falsified / too_early / inconclusive>
**Evidence:** <1-3 sentences summarizing what the data showed>
**KR impact:** <did the linked goal's KR move? By how much?>
**Learning:** <one sentence: what does this outcome teach us about this kind of decision?>
**Re-check:** <if too_early: when to check again>
EOF
```

Also update the palace node:
```python
# Update metadata: outcome, outcome_date, verdict
```

### 5. Update goal KR progress

If verdict is confirmed or partially_confirmed, note the KR progress on the linked goal's palace node:

```python
# Read G-xxx palace node, append KR progress note to content
# Update metadata->>'last_kr_update' = today
```

### 6. Surface to Akien

```
/outcome D-xxx — <verdict>
Hypothesis: "<hypothesis text>"
Evidence: <summary>
KR impact: <goal G-xxx — <kr_note>>
Learning: <learning>
```

If **falsified**: surface prominently. A falsified hypothesis is *good data* — it rules out a wrong bet. Ask: "Does this change any pending decisions or active tickets?"

If **too_early**: set a calendar note or slate entry for the re-check date.

---

## Steps — /outcome (list mode)

```bash
psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT path, title, metadata->>'date'
   FROM adc.palace
   WHERE path LIKE 'palace.decisions.%'
     AND metadata->>'outcome' IS NULL
     AND metadata->>'status' = 'open'
   ORDER BY updated_at ASC
   LIMIT 20"
```

Print as:
```
Decisions with unreviewed hypotheses (N):
  D-xxx (filed YYYY-MM-DD): <title>
  D-yyy (filed YYYY-MM-DD): <title>
```

---

## Hard rules

- Never skip /outcome because the outcome is obvious — "obvious" outcomes written down are the ones that teach you the most when they turn out to be wrong later.
- Falsified is not failure. A falsified hypothesis is the system working correctly — it caught a wrong bet before it propagated.
- too_early is not a dodge — always pair it with a re-check date.
- Learning is mandatory, even one sentence. That's the compounding value.

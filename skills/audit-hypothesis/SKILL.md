---
name: audit-hypothesis
description: Hypothesis-time audit — 5 checks before /sorted files tickets. Catches untestable claims, unobservable measurements, invalid goal links, contradictions with recent falsified hypotheses, and missing time horizons. Returns PASS / AMEND. Model: Opus.
model: opus
---

# /audit-hypothesis — Hypothesis quality gate

A hypothesis is only useful if it can be tested. This audit runs between
hypothesis extraction (in /sorted Step 2.6) and ticket filing (Step 3).
A vague hypothesis produces no outcome data — it's a bet with no payoff.

## Invocation

Called automatically by /sorted after hypothesis extraction. Also standalone:
```
/audit-hypothesis          — audit the hypothesis just extracted in conversation
/audit-hypothesis D-xxx    — audit the hypothesis stored on a filed decision
```

## Inputs

- **hypothesis text** — the testable claim ("what should be observably different after these tickets ship?")
- **measurement signal** — how we'll know ("the metric / behavior / log line")
- **goal link** — G-xxx this serves
- **time horizon** — when we'd check the outcome

For standalone on a filed decision:
```bash
F=$(ls "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}"/devlab/runtime/memory/decisions/*<D-id>*.json | head -1)
python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['body'].get('text',''))" "$F" | grep -A 10 "## Hypothesis"
```

---

## The 5 checks

### Check 1 — Testable claim, not a vague hope

**Look for:** the hypothesis makes a specific, falsifiable prediction about observable system behavior. It can be stated as "after these tickets ship, [observable thing] will [change in measurable way]."

**Fail when:** the hypothesis is a hope ("Igor will be better"), an intention ("we will improve X"), or unfalsifiable ("the system will feel more stable").

**AMEND:** "Hypothesis is not falsifiable — it can't be proven wrong. Restate as a specific prediction: 'After these tickets ship, [observable X] will [change Y by Z].' If you can't fill in the blanks, the hypothesis isn't ready."

---

### Check 2 — Measurement signal is observable now

**Look for:** the named signal can be queried or observed with current infrastructure — an existing log, DB column, metric, or behavioral test. Not "we'll add instrumentation" or "once we build the dashboard."

**Fail when:** the measurement requires future infrastructure that isn't in scope for this decision's tickets.

**AMEND:** "Measurement signal '<signal>' requires infrastructure not yet built. Either (a) add a ticket to this decision that creates the measurement capability, or (b) name a proxy signal that exists today."

---

### Check 3 — Goal link is valid and active

**Look for:** G-xxx names a goal that exists in palace and has status=active. The hypothesis plausibly advances one of that goal's KRs.

**Fail when:** goal doesn't exist, is retired, is blocked, or the hypothesis doesn't plausibly connect to the goal's KRs.

**AMEND:** "Goal link G-xxx is invalid (not found / retired / blocked). Either link to an active goal or acknowledge this decision is ungated (explicit `goal: none` with reason)."

---

### Check 4 — Doesn't contradict a recently falsified hypothesis

Read recent decision outcomes:
```bash
psql "$UU_HOME_DB_URL" -tAc \
  "SELECT path, title, metadata->>'outcome', metadata->>'hypothesis'
   FROM adc.palace
   WHERE path LIKE 'palace.decisions.%'
     AND metadata->>'outcome' = 'falsified'
     AND updated_at > now() - interval '60 days'
   ORDER BY updated_at DESC LIMIT 10"
```

**Look for:** no recently falsified hypothesis made the same or very similar claim.

**Fail when:** a hypothesis was falsified in the last 60 days that contradicts or is nearly identical to this one, with no acknowledgment.

**AMEND:** "Decision D-xxx had a similar hypothesis falsified on <date>: '<prior hypothesis>'. Acknowledge what's different about this attempt, or this is the same bet twice."

---

### Check 5 — Time horizon for checking outcome is named

**Look for:** a concrete point in time or event after which /outcome will be run — "7 days after last ticket closes", "at next weekly retro", "when instance.proposals has >10 real rows."

**Fail when:** no time horizon is stated, or it's open-ended ("we'll check eventually").

**AMEND:** "Time horizon for outcome check missing. Name when /outcome will run: a date, a calendar event, or a trigger condition. Without it, the hypothesis never gets evaluated."

---

## Challenge (always, after all 5 checks)

```
CHALLENGE: Is there a better hypothesis for achieving this goal?
  - Is there a simpler prediction that would tell you the same thing?
  - Is there a more direct path to the goal's KR that this decision doesn't address?
  - Could the measurement signal be stronger (less proxy, more direct)?
```

Advisory only — never blocks PASS.

---

## Output shape

### PASS

```
audit-hypothesis: PASS
Checks: 5/5 passed
Hypothesis: "<hypothesis text>"
Signal: <measurement signal>
Goal: G-xxx
Outcome check: <time horizon>
CHALLENGE: <challenge note or "hypothesis looks well-formed">
```

### AMEND

```
audit-hypothesis: AMEND
Checks: <N>/5 passed; <M> AMEND

AMEND items:
  Check <#> — <name>: <one-line failure>
    Fix: <suggested rewrite>

CHALLENGE: <challenge question>
```

---

## Hard rules

- Run all 5 checks — don't stop at first failure.
- Challenge always runs.
- AMEND blocks /sorted ticket-filing the same way audit-design does.
- "goal: none" is a valid explicit choice — but it must be stated, not defaulted.

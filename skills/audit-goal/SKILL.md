---
name: audit-goal
description: Goal-time audit — 7 checks on any G-xxx goal plus a mandatory "is there a better way?" challenge. Called after /goal new or /goal update. Returns PASS / AMEND. Model: Opus.
model: opus
---

# /audit-goal — Goal quality gate

Every goal gets audited before being treated as authoritative. A vague or
unmeasurable goal produces vague decisions and untestable hypotheses.

## Invocation

```
/audit-goal G-xxx          — audit a specific goal
/audit-goal                — audit all active goals (batch mode)
```

## Inputs

Read the goal from palace:
```bash
psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT title, content, metadata FROM adc.palace WHERE path = 'palace.goals.<slug>'"
```

Also read active goals list for conflict detection:
```bash
psql postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001 -tAc \
  "SELECT path, title, metadata->>'tensions' FROM adc.palace WHERE path LIKE 'palace.goals.%' AND metadata->>'status' = 'active'"
```

---

## The 7 checks

### Check 1 — Positive target (approach-frame)

**Look for:** target statement is phrased as movement *toward* something. Starts with a verb describing a desired state.

**Fail when:** target opens with `no`, `don't`, `never`, `avoid`, `stop`, `prevent`, or names only an absence.

**AMEND:** "Target is framed as a prohibition. Reframe to a positive state: instead of 'stop pe_chain failing', try 'pe_chain achieves >40% HYPOTHESIZE success rate'."

---

### Check 2 — At least one measurable Key Result

**Look for:** at least one KR that can be checked *today* against observable data — a count, rate, boolean, time delta. Not "improve" or "better."

**Fail when:** all KRs are vague ("more reliable", "works well", "improves") or require future infrastructure to measure.

**AMEND:** "KR '<kr>' is not measurable with current observability. Name the specific query, log line, or metric that would return a number today."

---

### Check 3 — Time horizon set

**Look for:** an explicit date or calendar reference (e.g. "2026-07-01", "end of Q3", "within 30 days of shipping T-xxx").

**Fail when:** no horizon is named, or horizon is "eventually" / "someday."

**AMEND:** "Time horizon missing. Without it, this goal never closes — it just accumulates. Set a date or a trigger event."

---

### Check 4 — Why-now is still valid

**Look for:** the `why_now` field states a condition that is currently true and that motivated setting this goal *now* rather than earlier or later.

**Fail when:** why_now is empty, generic ("it's important"), or describes a condition that has already resolved.

**AMEND:** "Why-now is stale or missing. What is true *right now* that makes this goal urgent? If the original why-now has resolved, re-examine whether the goal is still the right one."

---

### Check 5 — Linked decisions actually point at this goal

**Look for:** decisions listed in `linked_decisions` have content consistent with advancing this goal's KRs. (For new goals with no linked decisions yet: check passes with a note.)

**Fail when:** linked decisions exist but their scope visibly diverges from the goal's target — e.g., a goal about pe_chain reliability has decisions about UI improvements linked to it.

**AMEND:** "Decision D-xxx is linked but its scope ('{scope}') doesn't advance KR '{kr}'. Either unlink it or explain the connection."

---

### Check 6 — Conflicts with other active goals named

**Look for:** if this goal's pursuit would consume resources, attention, or architectural decisions that conflict with another active goal, the `tensions` field names it.

**Fail when:** two active goals obviously compete (e.g., "ship features fast" vs. "zero regressions") but neither's tensions field acknowledges the other.

**AMEND:** "This goal conflicts with G-xxx ('...'). Name the tension explicitly in the tensions field — managed tensions are navigable; unnamed ones cause silent drift."

---

### Check 7 — Falsification condition exists

**Look for:** a concrete statement of what evidence would cause this goal to be *retired or pivoted* rather than continued — the conditions under which continuing would be wrong.

**Fail when:** no falsification condition is named, or it's circular ("we'd abandon this goal if it doesn't work").

**AMEND:** "Falsification condition missing or circular. Finish this sentence: 'We would stop pursuing this goal if ___.' Goals without exit conditions never die — they just get more expensive."

---

## Challenge (always, after all 7 checks)

Regardless of PASS or AMEND, always ask:

```
CHALLENGE: Is there a better way to achieve what this goal is trying to achieve?
  - Is the goal framed at the right level of abstraction?
  - Is there a goal that would make this one unnecessary?
  - Is there a simpler KR that would tell us what we actually need to know?
```

This is advisory — it never blocks PASS. Surface it for Akien's consideration.

---

## Output shape

### PASS

```
audit-goal: PASS
Goal: G-xxx — <title>
Checks: 7/7 passed
CHALLENGE: <one-line challenge question or "goal framing looks right">
```

### AMEND

```
audit-goal: AMEND
Goal: G-xxx — <title>
Checks: <N>/7 passed; <M> AMEND

AMEND items:
  Check <#> — <name>: <one-line failure>
    Fix: <suggested rewrite>

CHALLENGE: <challenge question>
```

---

## Hard rules

- Run all 7 checks even if early ones fail — a complete AMEND list is more useful than a partial one.
- Challenge always runs, even on PASS.
- AMEND blocks treating the goal as authoritative until Akien applies fixes or explicitly overrides.

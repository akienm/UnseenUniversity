---
name: audit-design
description: Filing-time decision audit. Called by /sorted after the decision summary, before tickets are drafted. Catches a "decision" that isn't actually decided — vague intention, unobservable success criteria, conflicts with prior decisions or palace rules, undecomposed scope, missing executor assignment. Returns PASS / AMEND. Standalone invocation also supported via `/audit-design <decision-id>` for re-checking an already-filed decision.
---

# audit-design — Decision-time positive checks

Fires between `/sorted` Step 2 (summarize) and Step 3 (draft tickets).
Reviews the decision narrative + scope context against nine positive
checks. Cheap relative to the cost of filing tickets that don't survive
their first sprint.

This audit operates on the *decision*, not on individual tickets — the
ticket-level checks live in `audit-ticket` (called per ticket in
/sorted Step 4).

---

## Inputs

- **Decision narrative**: the 1–2 sentence summary written in /sorted Step 2.
- **Scope context**: the conversation turns since the most recent `DESIGN_START`, prior `/sorted`, or session start.
- **Optional arg**: `<decision-id>` to audit an already-filed decision retroactively.

---

## Checks

Each check has a positive shape (what to look for) and an AMEND message
(what to ask the decider when the check fails).

### Check 1 — Intention stated as observable target

**Look for:** the decision summary (or root intention field) names a thing to move
*toward*, in approach-frame shape (`unseenuniversity/rules/approach-frame`). Phrased
as "I intend that..." or a verb describing the desired observable state.

**Fail when:** the summary opens with `no`, `don't`, `never`, `avoid`, `stop`,
`prevent`, or names only an absence with no positive target.

**AMEND:** "Decision names a prohibition without naming what to do instead.
Reframe to a positive observable target — `<rewrite suggestion based on context>`.
See `unseenuniversity/rules/approach-frame`."

### Check 2 — Success condition observable in the runtime

**Look for:** the narrative names a runtime-observable signal that confirms
the decision shipped — a log line, a behavioral change, a metric, a UI element,
a database row, an end-to-end flow producing user-facing text. Not just
"tests pass" or "code merged."

**Fail when:** the only success criterion is unit-test pass / merge / structural.

**AMEND:** "Decision needs a runtime-observable success condition. What user-facing
or system-observable signal will confirm this shipped? See
`unseenuniversity/rules/budget` (verify-end-to-end-before-flipping-gates principle)."

### Check 3 — Alternatives + why this one

**Look for:** at least one alternative considered, with a one-line reason
why the chosen path won. For LOW-stakes decisions, a single line ("considered X,
chose Y because Z") is enough; for HIGH-inertia decisions, expect more depth.

**Fail when:** no alternative is named, or "we just decided X" without context.

**AMEND:** "Name at least one alternative considered. Even a one-line
'considered X, chose Y because Z' protects against decisions that look obvious
in hindsight but weren't."

### Check 4 — Constraints named

**Look for:** the narrative explicitly names HIGH-inertia files touched (if any),
gates the work depends on (`<a real gate flag>`, etc.), dependencies on other
pending tickets, and any deadline or freeze-window constraints.

**Fail when:** scope obviously involves HIGH-inertia areas (`unseen_university/`,
`unseen_university/devices/bus/`, `device.py`, `shim.py`) or a known gate but the
narrative is silent on them.

**AMEND:** "Decision touches `<area>` which is HIGH-inertia / gated by `<flag>` —
narrative must name that constraint. See `unseenuniversity/rules/safeguards`."

### Check 5 — "What am I missing / what could be better" answered

**Look for:** evidence in the scope (the conversation) that these two questions
were asked at design time and produced concrete content — additions, corrections,
or explicit "nothing else" with reasoning.

**Fail when:** the conversation jumped from initial framing to /sorted without
the closing-question pass.

**AMEND:** "Run the closing pass before /sorted: 'What am I missing? What could
we do better?' These two questions reliably surface gaps. Akien's standing
practice — see `unseenuniversity/rules/collaboration`."

### Check 6 — Conflicts with last-30d decisions

**Look for:** the decision is consistent with decisions filed in the last 30
days. Read the recent decisions log:

```bash
ls devlab/runtime/memory/decisions/cc.0.D*.json 2>/dev/null | sort -r | head -30 | xargs -I{} python3 -c "import json,sys; r=json.load(open('{}')) ; b=r.get('body',{}); print(b.get('line', r.get('id','?')))" 2>/dev/null
```

**Fail when:** the new decision contradicts or undoes a recent one without
acknowledging it.

**AMEND:** "Decision conflicts with `D-<id>` (`<one-line summary>`). State the
override explicitly — what changed since that decision that justifies the new
direction?"

### Check 7 — Palace-rule conflicts

**Look for:** the decision honors all current palace rules. Check against:
- `unseenuniversity/rules/database` — Postgres only, no SQLite/file-store fallbacks
- `unseenuniversity/rules/coding` — OOP-first when shared state crosses functions
- `unseenuniversity/rules/docs-live-in-code` — load-bearing touches name primary file's docstring
- `unseenuniversity/rules/memory` — memory distinctions become tags, not new types
- `unseenuniversity/rules/igor-constraints` — no speculative `IGOR_*_ENABLED` flags
- `unseenuniversity/rules/inherit-base-class` — every non-library class inherits from base (when this rule lands; gate Check 7 on its presence)

**Fail when:** any rule conflict is present in the decision narrative or scope.

**AMEND:** "Decision conflicts with `<rule>`: `<specific gap>`. Either honor the
rule or document why this decision is the explicit exception (rare — exceptions
need their own /sorted)."

### Check 8 — Scope decomposed into atomic ticketable units

**Look for:** the decision implies units of work that fit in one PR each.
Each unit has a clear single intention — not "build subsystem X" but "build the
schema piece of subsystem X" + "build the helper piece of subsystem X" etc.

**Fail when:** scope is one monolithic "do the whole thing" with no
decomposition.

**AMEND:** "Decision is too coarse for ticketing. Decompose into atomic units
(one PR each) before filing. Sketch the unit list inline so /sorted Step 3
has discrete drafts to work with."

### Check 9 — Per-piece executor + inertia tier named

**Look for:** for each implementation unit, the narrative or scope names who
executes it (Igor / CC / Akien) and what inertia tier it sits in (HIGH /
MEDIUM / LOW).

**Fail when:** the decision implies multiple pieces but executor/tier
assignments are missing.

**AMEND:** "Per-piece executor and inertia tier missing. Igor handles MEDIUM
and LOW; CC authors HIGH-inertia diffs. Without this, /sprint can't route the
work. See `unseenuniversity/rules/coding` (inertia tiers) + `unseenuniversity/rules/collaboration`
(executor routing)."

---

## Output shape

### PASS

```
audit-design: PASS
Decision: <D-id or "draft">
Checks: 9/9 passed
Telemetry: unseenuniversity/audits/design/runs/<timestamp>
```

### AMEND

```
audit-design: AMEND
Decision: <D-id or "draft">
Checks: <N>/9 passed; <M> AMEND

AMEND items:
  Check <#> — <name>: <one-line failure summary>
    Fix: <suggested rewrite or addition>
  Check <#> — <name>: <one-line failure summary>
    Fix: <suggested rewrite or addition>

Telemetry: unseenuniversity/audits/design/runs/<timestamp>
```

When invoked from `/sorted`: AMEND blocks Step 3 (drafting) until the
decider applies the amendments and re-runs `/sorted` (or the audit is
explicitly overridden inline).

---

## Steps

### 1. Read inputs

When called from `/sorted`: the decision summary + scope are passed in
the parent context. When called standalone with `<decision-id>`: read the
decision JSON at `devlab/runtime/memory/decisions/*<decision-id>*.json` (the
narrative is in `body.text`) plus recent decisions from the same directory.

### 2. Read prior watch-for notes

```
memory_get(path="unseenuniversity/audits/design/watch_next/<active-notes>")
```

For each active note, check whether this decision matches the watch
condition. Hits get logged; expirations age automatically. (Until the
audit_telemetry helper ships, this step is documented but not yet active —
see T-audit-telemetry-shape.)

### 3. Run the nine checks

Each check returns PASS or AMEND-with-message. Stop-on-first-fail is NOT
enabled — run all nine even if early checks fail, so the AMEND output is
complete in one round.

### 4. Detect HIGH-inertia surface

When the decision narrative names files in HIGH-inertia areas
(`unseen_university/`, `bus/`, `device.py`, `shim.py`) or any
file flagged HIGH in the subsystem index, surface inline for Akien
pre-approval before /sorted proceeds. The HIGH-inertia mention is not an
AMEND — it's a separate gate.

### 5. Write watch-for notes

When a check passes only marginally (e.g., success-condition is observable
but vague), write a watch-for note at
`unseenuniversity/audits/design/watch_next/<id>` so the next decision in the same
area gets tighter scrutiny. (Telemetry helper required — see
T-audit-telemetry-shape.)

### 6. Emit run record

Per `unseenuniversity/audits/<level>/runs/<timestamp>` shape (see
T-audit-telemetry-shape):

```yaml
level: design
ran_at: <iso>
inputs_examined: 1
checks_fired: 9
checks_passed: <N>
checks_amended: <M>
checks_discarded: 0
findings:
  - check: <id>
    severity: low|med|high
    file_or_target: <decision-id or "draft">
    matched_pattern: <one line>
    upstream_layer: origin
    overridden: false
duration_seconds: <float>
tokens_used: <int>
model: opus
watch_next_written: <count>
watch_next_hit: <count>
notes: <free text>
```

Until `audit_telemetry.emit_run_record()` ships, the run record is described
in this step but not yet emitted programmatically. Until then, the audit's
PASS/AMEND verdict stands as its own record in the parent /sorted output.

### 7. Challenge (always — advisory, never blocks PASS)

After all checks, always ask:

```
CHALLENGE: Is there a better architectural approach to achieve this goal?
  - Name one alternative architecture or design that was considered and rejected.
  - If no alternative was considered: is this the first design that came to mind,
    or the best one? What would change if you had to argue for a different approach?
  - Could a simpler design achieve the same goal with fewer moving parts?
```

Output labeled `CHALLENGE:` — never labeled AMEND. Akien can proceed without acting on it.
This fires on every run, PASS or AMEND, no exceptions.

### 8. Return verdict

PASS or AMEND, in the output shape above. /sorted gates Step 3 on this
return.

---

## Override

When Akien explicitly overrides an AMEND inline ("ignore Check 6, the prior
decision is being deliberately reversed and the override note will land in
the new decision narrative"), record the override in the run record:

```yaml
findings:
  - check: 6
    overridden: "deliberate reversal of D-prior-id; override note in narrative"
```

Overrides are not failures — they're informed decisions to proceed. The
record lets `audit-audits` spot patterns (a check that gets overridden 50%
of the time is probably mis-shaped).

---

## Hard rules

- Always run all nine checks; don't stop on first fail.
- Always surface HIGH-inertia file mentions inline for Akien pre-approval.
- Always emit a run record (or document the verdict equivalently until the
  telemetry helper lands).
- An AMEND from this audit blocks /sorted Step 3 until applied or
  explicitly overridden.

---

## Standalone invocation

```
/audit-design <decision-id>
```

Re-runs the audit on a previously-filed decision. Useful for retroactive
quality checks during /day-close-audit or `audit-audits` analysis windows.
Output shape is identical; the run record carries the same `level: design`
tag.

---

## Why Opus

This audit operates on free-form decision text, not on declarative
ticket fields. The judgment calls — does this success criterion count as
"observable in runtime"? Does this pass the conflict check against a
30-day window of prior decisions? — are exactly the work where Sonnet
tends to either rubber-stamp or over-flag. Opus runs cost more per
invocation, but `/sorted` only fires a few times per day, and the
downstream cost of bad-decision tickets is much higher than this audit's
token bill.

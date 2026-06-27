---
name: audit-audits
description: Meta-audit. Consumes structured telemetry from every audit layer (design / ticket / precode / smell / debris / day / expert) and analyzes patterns over time — recurring smells worth promoting to palace rules, upstream-miss accumulation (catches that should land one layer earlier), watch-for ROI, dead checks, false-positive sweeps, cost-per-finding, habit health, cross-layer coherence. Runs on the telemetry corpus, NOT on the codebase. Cadence: Sonnet weekly, Opus monthly.
model: sonnet
---

# audit-audits — Meta-audit over telemetry

> **Status: design spec, not yet built.** The analyzer engine is unimplemented,
> pending `T-audit-telemetry-shape` and the flat-file telemetry substrate under
> `devlab/runtime/memory/`. Treat the steps below as intended design until it ships.

The pyramid catches code drift. `audit-audits` catches *audit drift* —
the system slowly becoming worse at watching itself. Without this layer,
checks that used to matter become noise; smells that should have been
promoted to rules stay as repeated catches; layers grow imbalanced.

`audit-audits` is the only layer that can see those patterns, because
it's the only layer reading *all* the audits' output as its input.

---

## Inputs (telemetry corpus, NOT codebase)

- `unseenuniversity/audits/<level>/runs/*` — every audit's per-run records,
  bounded by analysis window (default 30 days; --since to override).
- `unseenuniversity/audits/<level>/watch_next/*` — active and recently-expired
  watch-for notes from every audit.
- `unseenuniversity/audits/overrides_log` — every override Akien stamped on a
  finding (with reason).
- SensorTree counter trends — `unseenuniversity/metrics/audit_health/` (created
  by this skill on first run).

**Hard rule: NO codebase reads.** The corpus IS the input. Re-reading
source defeats the cost model.

---

## Analyses

Eight analyzers. Each produces zero or more candidate outputs (rule
promotions, retirements, rebalances, expirations). Akien gates the
candidates through `/sorted` before any palace write fires.

### Analyzer 1 — Recurring smell promotion

**Signal**: A smell finding (any audit layer) that fires ≥3 times across
≥2 distinct tickets in the analysis window, AND was not overridden in
≥80% of the cases.

**Output**: A candidate palace rule promotion. Format:

```yaml
candidate: rule_promotion
finding: <check name>
window: <N> hits across <M> tickets
override_rate: <pct>
proposed_rule: unseenuniversity/rules/<name>
proposed_check_shape: unseenuniversity/rules/ticket_design_checks/<name>
draft_narrative: <suggested first paragraph>
draft_check_body: |
  applies_when: <inferred>
  check_body: <inferred>
  failure_message: <inferred>
```

`/sorted` then files the promotion as a ticket.

### Analyzer 2 — Upstream-miss accumulation

**Signal**: Finding `upstream_layer` field consistently points one layer
*later* than where the catch lands. Example: audit-smell catches a try/except
without log; the `upstream_layer` says `audit-ticket` (the ticket should
have declared logging in advance).

**Output**: A candidate layer-rebalance proposal. Format:

```yaml
candidate: layer_rebalance
catch_layer: audit-smell
upstream_layer: audit-ticket
finding: <check>
window: <N> hits, all attributed upstream
proposal: tighten <upstream check> to require <X>
```

This is how the pyramid self-tunes — when a layer keeps catching things
the layer above should have, the upstream check needs sharpening.

### Analyzer 3 — Watch-for note ROI

**Signal**: For every active and recently-expired watch-for note across
all audits, count hits in the window.

- Hits ≥2 → **promotion candidate** (note becomes a check)
- Hits = 0 in 14 days → **expire** (already automated)
- Hits = 1 in 14 days → **age** (extend TTL by 7 days)

**Output**: For each promotion candidate, a draft check shape (same
format as Analyzer 1). For each expiration, a no-op (auto-aged).

### Analyzer 4 — Dead-check retirement

**Signal**: A check that fired ≥10 times in window AND passed every time
(no AMEND), OR fired ≥10 times AND was overridden every time.

**Output**: A candidate check-retirement. Format:

```yaml
candidate: check_retirement
check: <id>
audit_layer: <layer>
window_fires: <N>
fail_rate: <pct>
override_rate: <pct>
proposal: retire OR tighten OR move to <layer>
```

Always-pass checks are dead weight; always-overridden checks are
mis-shaped. Either way, action.

### Analyzer 5 — False-positive sweeps

**Signal**: Overrides clustering on a single check at >40% rate within
the window. The check is mis-shaped: it fires when it shouldn't, and
the human has to clear it every time.

**Output**: A candidate check-tightening. Format:

```yaml
candidate: check_tighten
check: <id>
audit_layer: <layer>
window_fires: <N>
override_rate: <pct>
override_reasons: [<top-3-clustered>]
proposal: <tighten the applies_when based on override patterns>
```

The override reasons (free-text Akien wrote at override time) are the
input to the tightening proposal. Sonnet reads them, drafts an
applies_when refinement.

### Analyzer 6 — Cost-per-finding

**Signal**: For each audit layer, compute (sum of tokens_used / count
of material findings). "Material" = HIGH or MED severity, not LOW.

**Output**: When a layer's cost-per-finding crosses 2x its 30-day
average, candidate model downgrade or check pruning. Format:

```yaml
candidate: cost_review
layer: <name>
window_tokens: <N>
material_findings: <M>
cost_per_finding: <ratio>
average_baseline: <ratio>
proposal: downgrade model | prune low-yield checks | re-scope skip-gate
```

This is how budget pressure surfaces before it bites.

### Analyzer 7 — Habit health

**Signal**: Cadence drift across audit invocations.

- audit-day skipped >2 days in window → flag
- audit-expert weekly rotation skipped → flag
- audit-audits monthly skipped → flag
- TTR (time to resolution) on HIGH findings rising trend → flag
- inflight slate items aged >5 days → flag

**Output**: Habit-health metric updates to
`unseenuniversity/metrics/audit_health/*` (counters + history per the SensorTree
shape from `unseenuniversity/rules/metrics`). No tickets — the metric IS the
output.

### Analyzer 8 — Cross-layer coherence

**Signal**: Layer X claims a property that Layer Y immediately contradicts.

Concrete patterns to check:
- audit-design said decision was "decomposed into atomic units" but
  audit-ticket flagged ≥30% of the spawned tickets for SPLIT.
- audit-precode said "all paths exist" but audit-smell caught
  fix-one-leave-many on the same diff.
- audit-ticket said "validation declared" but audit-debris caught test
  cleanup leaks the validation should have prevented.

**Output**: A candidate upstream-tightening proposal naming the gap:

```yaml
candidate: cross_layer_coherence
upstream_layer: <X>
upstream_claim: <what it asserted>
downstream_layer: <Y>
contradiction: <what Y caught>
window: <N> instances
proposal: tighten <X>'s check on <property>
```

This is the gap-detection lens — not "did the rules work" but "did the
audits agree with each other."

---

## Steps

### 1. Determine cadence + window

```bash
# Default: weekly (Sonnet) cadence, 7-day window
# Monthly (Opus) cadence: 30-day window
WINDOW_DAYS=${AUDIT_AUDITS_WINDOW:-7}
SINCE=$(date -d "$WINDOW_DAYS days ago" -Iseconds)
```

`/audit-audits --window 30` overrides for the monthly run. `/audit-audits
--since <iso>` overrides for ad-hoc analysis.

### 2. Read the corpus

```bash
# Audit telemetry lives in the flat-file store (shape locked by T-audit-telemetry-shape).
find "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/" \
  -path '*/audits/*/runs/*.json' -newermt "<since>" | sort
```

Plus watch_next nodes, plus the overrides_log.

### 3. Run the eight analyzers

Each analyzer is a method on
`AuditAuditsEngine(BaseDevice)`. They run independently — no shared
mutable state — and each returns a list of `Candidate` records.

### 4. Aggregate candidates

Sort by `(severity, expected_impact)` descending. Severity here means:
- **CRITICAL**: cost-per-finding spike, habit-health drift in HIGH-impact
  audits — surfaces immediately.
- **HIGH**: rule promotion, dead-check retirement, layer rebalance.
- **MED**: false-positive tightening, watch-for promotion.
- **LOW**: cross-layer coherence reports (informational).

### 5. Write metric updates

For Analyzer 7 (habit health), update SensorTree counters at
`unseenuniversity/metrics/audit_health/*` directly. No `/sorted` gate — these
are observations, not changes.

### 6. Write candidate proposals

For Analyzers 1, 2, 4, 5, 6, 8: write each candidate to
`unseenuniversity/audits/audits/candidates/<YYYY-MM-DD-HHMMSS>-<id>` palace
nodes. These are drafts — `/sorted` reads them and decides whether to
file as tickets, override, or discard.

### 7. Emit run record

`audit-audits` IS an audit, so it emits its own run record at
`unseenuniversity/audits/audits/runs/<timestamp>`. The recursion ends at one
level — `audit-audits` analyzes the corpus including its own prior
runs, but doesn't recurse into its own output.

### 8. Report

Print a human-readable summary to stdout:

```
audit-audits: window <N>d, <M> runs analyzed
Candidates: <K> CRITICAL, <L> HIGH, <P> MED, <Q> LOW
  CRITICAL: <one-line>
  HIGH: <one-line>
  ...
Metric updates: <count> counters, <count> history rows
Watch-for: <promoted>, <expired>, <aged>
Telemetry: unseenuniversity/audits/audits/runs/<timestamp>

Next: review candidates at unseenuniversity/audits/audits/candidates/* via /sorted.
```

---

## Helper engine: devlab/claudecode/audit_audits_engine.py

Not yet built. The engine — `AuditAuditsEngine(BaseDevice)` with one method per
analyzer (returning frozen `Candidate` records) and its tests — lands when this
skill ships, against the telemetry schema locked in `T-audit-telemetry-shape`.
Until then this skill is a design spec, not a runnable tool.

---

## Cadence

- **Weekly** (Sonnet): `/audit-audits --window 7` — runs every Monday
  morning. Catches cost-per-finding spikes early, surfaces high-impact
  promotions while context is fresh.
- **Monthly** (Opus): `/audit-audits --window 30` — runs first of each
  month. Wider window catches slower-moving patterns: cross-layer
  coherence, dead-check retirements, layer rebalances. Opus's deeper
  judgment is justified at this cadence.
- **On-demand**: ad-hoc invocation with `--since <iso>` for spot
  analysis.

The cadence runs are scheduled via `/schedule` (built-in) at the
appropriate cron frequency.

---

## Why audit-audits exists

Without this layer, the audit pyramid silently degrades:

1. Checks that used to matter become noise (always-pass / always-override).
2. Smells that should have been promoted to rules stay as repeated catches.
3. Watch-for notes accumulate forever without value tracking.
4. Layers grow imbalanced (one layer doing all catching, others empty).
5. Cost rises without scrutiny (Sonnet escalations creep into the cheap
   tier).

`audit-audits` is the only layer that can see all of this, because it's
the only layer with all the audits' output as its input. The lever:
every audit MUST emit the structured run record. Cheap requirement,
compounds heavily.

---

## What this layer does NOT do

- **Does not write to palace rules directly.** All proposals go through
  `/sorted`. Akien stays in the loop on rule changes.
- **Does not read source code.** The corpus is structured telemetry;
  any grep/walk of source belongs in audit-day or audit-expert.
- **Does not retire its own checks.** If audit-audits' analyzers become
  dead weight, that's a human-noticed event — recursion-self-pruning
  is a footgun.
- **Does not resolve overrides automatically.** Overrides are signals,
  not noise; they feed Analyzer 5 (false-positive sweeps).

---

## Hard rules

- Always run all eight analyzers; don't stop on first material finding.
- Always emit candidates as palace nodes (drafts), never as filed
  tickets. /sorted is the gate.
- Always update habit-health metrics directly (no /sorted) — those are
  observations, not changes.
- Always emit a self-run record (recursion ends at 1 level).
- Always read from telemetry corpus only; no codebase reads.

---

## Standalone invocation

```
/audit-audits                     # default 7-day window
/audit-audits --window 30         # monthly run
/audit-audits --since <iso>       # ad-hoc range
/audit-audits --analyzer recurring_smells  # single-analyzer focus
```

Useful when investigating a specific noise pattern or after a known
audit-process drift event (a check producing many overrides, a layer
suddenly silent).

---

## Why Sonnet (weekly) / Opus (monthly)

- **Weekly** is pattern-matching over structured input — Sonnet handles
  this well. Cost-per-finding lookups, override clustering, simple
  threshold checks. Volume is low; cost is contained.
- **Monthly** is judgment work — does this recurring smell deserve a
  rule promotion? Is this layer-rebalance proposal coherent across the
  pyramid? Is this cross-layer contradiction real or coincidence? Opus
  earns its tokens here. Cadence is monthly, so cost is bounded.

Tickets / proposals filed by audit-audits are themselves audited at
filing time by audit-design (via `/sorted`) and audit-ticket. The
pyramid checks audit-audits' output the same way it checks any other
decision.

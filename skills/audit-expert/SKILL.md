---
name: audit-expert
description: Expert-lens audit — each of 11 specialists applies the broadest view of their discipline. Weekly cadence rotates 3 randomly-selected experts; monthly runs the full panel. Each expert emits ≤5 severity-tagged observations, ≤2 watch-for-next-time notes (to palace), and 0-1 candidate ticket drafts (via audit-ticket). Replaces /deep-audit with a structured telemetry-emitting version. Model: Opus per expert.
model: opus
---

# audit-expert — Broadest-lens expert review

Where `/audit-day` is the process lens, `audit-expert` is the discipline
lens. Each expert sees the whole codebase through their field's sharpest
questions — not "is this code clean?" but "is this system doing what this
discipline demands?"

## Invocation

```
/audit-expert                      # weekly: 3 random experts
/audit-expert --mode=monthly       # full 11-expert panel
/audit-expert --experts=safety,ux  # explicit selection
/audit-expert --area=architecture   # pick experts for recently-touched area
```

## The 11 experts

| # | Expert | Broadest lens |
|---|--------|---------------|
| 1 | **Cognitive Scientist** | Is Igor's reasoning architecture consistent with human cognition models? Salience, attention gating, working memory limits, predictive coding alignment. |
| 2 | **Systems Architect** | Is the subsystem decomposition clean? Coupling, cohesion, contract boundaries, failure isolation, blast radius. |
| 3 | **Safety Engineer** | What are the failure modes? Correlated failures, loop risks, runaway processes, unrecoverable states. |
| 4 | **Human-Computer Interaction** | Is Igor legible to its users? Feedback quality, error message clarity, response timing, trust signals. |
| 5 | **Distributed Systems** | Is the multi-instance / multi-machine design sound? Consistency, partition tolerance, idempotency, clock drift. |
| 6 | **Machine Learning Engineer** | Is the learning architecture coherent? Training data quality, distribution shift, feedback loop health, cold-start. |
| 7 | **Process / Meta Engineer** | Is the development process self-improving? Audit ROI, test coverage trend, tech debt accumulation rate, velocity. |
| 8 | **Security Engineer** | What can go wrong from adversarial inputs? Injection paths, trust boundary violations, secret exposure, audit trail completeness. |
| 9 | **Reliability Engineer** | What does the on-call story look like? MTTR, alerting gaps, runbook completeness, graceful degradation. |
| 10 | **Data Engineer** | Is the persistence layer sound? Schema drift, migration safety, index health, data lineage, retention policy. |
| 11 | **Product Manager** | Is Igor actually making progress toward its stated goal? Ticket velocity, blocker patterns, scope creep, capability map vs reality. |

## Per-expert output format

Each expert produces:

```
EXPERT: <name>
Observations (≤5, severity-tagged):
  HIGH: <observation — specific, actionable>
  MED:  <observation>
  LOW:  <observation>

Watch-for-next-time (≤2):
  - <pattern to watch in the next 7 days>

Candidate ticket (0-1):
  <ticket draft → run through /audit-ticket before filing>
```

## Steps

### 1. Load context (shared across all experts)
```bash
# Recent commits
git log --oneline --since="7 days ago"
# Today's stats
# TODO(UU): map_igor.py is TheIgors-only; needs a UU data source
# python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/map_igor.py --section=tickets
# python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/map_igor.py --section=gates
# Prior watch-for notes for expert level
python3 -c "
from devlab.claudecode.audit_telemetry import read_watch_next
for n in read_watch_next('expert'):
    print(n['path'], n['content'][:100])
"
```

### 2. Select experts

Always load the expert panel from the project root first:
```bash
python run load .
```
This reads `EXPERTS.md` if present; falls back to the default 11-expert list when absent.
The loaded JSON is the authoritative panel for this run — use it for all selection below.

Select based on invocation mode:
```bash
python run select weekly .              # 3 day-seeded random experts
python run select monthly .             # all experts in the loaded panel
python run select experts:Safety,HCI .  # named experts (partial match)
python run select area:architecture .   # area-mapped experts (see _AREA_MAP in run script)
```

- **Weekly**: choose 3 experts at random from the loaded list (day-of-week seed — deterministic within a day).
- **Monthly**: all experts in the loaded panel.
- **On-demand with --experts**: the named ones (partial name match).
- **On-demand with --area** (maps to `_AREA_MAP` in the run script):
  - `architecture` → Systems Architect, Security Engineer, Self-Improving Systems
  - `process` → Process / Meta Engineer, Product Manager, Reliability Engineer
  - `observability` → Human-Computer Interaction, Evaluator Quality, Observability-First

### 3. Run each expert (Opus, one at a time)
For each selected expert, apply their broadest lens to the codebase context.
Output per the format above.

When an observation is HIGH severity: immediately check if a ticket already
covers it (`/audit-ticket` duplicate check). If not → draft a candidate ticket.

### 4. Write watch-for notes
For each watch-for observation:
```python
from devlab.claudecode.audit_telemetry import emit_watch_next
emit_watch_next("expert", "<note>", ttl_days=7)
```

### 5. Check prior watch-for hits
Any watch-for note from a prior run that matches today's context →
mark as hit in the telemetry record.

### 6. Ultraview integration
Big PRs / monthly panel / pre-merge on HIGH-inertia:
```
/ultraview          # only on staging branches or with explicit Akien trigger
```

### 7. Synthesize and post
After all experts run:
- Aggregate findings by severity across all experts
- Deduplicate overlapping observations (same root cause, multiple experts)
- Post summary to cc_channel via `_post_to_channel`
- Emit per-run telemetry record

### 8. Emit telemetry
```python
from devlab.claudecode.audit_telemetry import emit_run_record, AuditRunRecord
record = AuditRunRecord(
    level="expert",
    checks_fired=len(selected_experts),
    checks_passed=...,
    findings=[...],
    model="opus",
    watch_next_written=N,
    watch_next_hit=M,
)
emit_run_record("expert", record)
```

## Cadence enforcement

| Trigger | Experts | Model | Ultraview |
|---------|---------|-------|-----------|
| Weekly (auto via /day-close on Fridays) | 3 random | Opus | No |
| Monthly (first Monday) | All 11 | Opus | Yes (on HIGH findings) |
| On-demand | Selected | Opus | On explicit flag |
| Pre-merge HIGH-inertia | 2-3 area-relevant | Opus | Yes |

## Hard rules
- Always emit telemetry, even on zero-findings runs — trend data requires uniform sampling.
- Watch-for notes always have TTL ≤ 14 days. No indefinite watches.
- Candidate tickets always go through `/audit-ticket` before filing — never auto-file.
- Ultraview is user-triggered only (it bills); never self-invoke without explicit ask.
- HIGH-severity findings from monthly panel → surface to Akien in the day-close Discussion post.

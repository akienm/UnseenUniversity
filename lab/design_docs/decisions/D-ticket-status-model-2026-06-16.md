# D-ticket-status-model-2026-06-16
**title:** Canonical ticket status model — 7 statuses, set in concrete
**date:** 2026-06-16
**status:** open
**spawned_tickets:** T-ticket-status-fold-design-triage (shipped 2026-06-16), T-ticket-status-approval-akien-removal, T-ticket-status-assigned-shim-nag

## Hypothesis
A small, fixed, documented set of ticket statuses — with this doc as the single
source of truth — stops the recurring failure where sessions invent new statuses
or misuse `triage`. Any status not on this list is a bug to be corrected, not a
new state to be honored.

## Measurement Signal
No tickets appear with statuses outside the canonical 7. The design+triage
double-categorization symptom disappears. New code and skills reference this doc
(or the constant derived from it), not ad-hoc strings.

## Goal Link
Serves observability + the self-improvement goal: a fixed status vocabulary is a
precondition for the queue device, for cost-aware dispatch, and for any automated
reasoning over ticket lifecycle. You cannot build a state machine on a vocabulary
that drifts.

## Why this doc exists (the problem)
Akien, 2026-06-15: *"we're revamping ticket status and we're gonna set it in
concrete somewhere because we've had a problem with inventing new ticket statuses
or using triage wrong."* And: *"yesterday we collapsed design and triage, but I'm
still seeing tickets with both categories."*

The status vocabulary had sprawled — `triage`, `design`, `open_questions`,
`approval`, `akien`, `awaiting_approval`, `needs_review`, `sprint`, `in_progress`,
`hold`, `dependency`, `escalated`, `pending`, `rejected` — with overlapping
meanings and no canonical list. This doc is the concrete.

---

## The canonical 7 statuses

| Status | Meaning | Who sets it |
|---|---|---|
| **TRIAGE** | Not yet ready to work. Needs review, design, or open questions answered. The single intake/incubation state. | anyone |
| **READY** | Reviewed and cleared to be worked. A builder may claim it. (Was `sprint`.) | anyone |
| **ASSIGNED** | Claimed by a builder but work has not started. The builder's shim has acknowledged it. Triggers a nag loop if idle >10 min. | dispatcher / builder shim |
| **INPROGRESS** | A builder is actively working it. | builder |
| **CLOSED** | Terminal. Covers done, won't-do, and rejected — all one state. | builder / Akien |
| **HOLD** | Explicitly paused. **Akien-only** — a builder may *request* a hold but may not set it. | Akien |
| **DEPENDENCY** | Blocked on a named dependency (another ticket ID or an Akien action). | anyone |

### TRIAGE — what folded in (Akien, 2026-06-15)
*"willing to collapse triage and open questions and even design."*
`triage`, `open_questions`, and `design` are ALL now `TRIAGE`. There is no separate
"needs design" or "has open questions" status — those are notes inside a TRIAGE
ticket, not statuses. `needs_review` also folds here (a ticket needing review is
not yet ready).

### READY vs ASSIGNED vs INPROGRESS — the split (Akien, 2026-06-15)
*"ASSIGNED and INPROGRESS are because you can be assigned something but deem
something else too important to complete before starting work… your shim will
reply for you, and it'll sit in assigned until you start work, or it times out and
leaves you alone for 10 mins before trying again."*

- **READY** — cleared, unclaimed. The queue's actionable pool.
- **ASSIGNED** — a builder has been handed this ticket and its shim acknowledged,
  but the builder hasn't started (it may be finishing something it judges more
  important). The shim replies on the builder's behalf. The ticket sits in
  ASSIGNED until the builder starts work (→ INPROGRESS) **or** the assignment
  times out at **10 minutes**, after which the dispatcher backs off and leaves the
  builder alone for 10 min before retrying.
- **INPROGRESS** — work is actually happening.

This ASSIGNED state + the 10-minute shim-nag/back-off loop is **net-new behavior**
that does not exist in code yet. It is ticketed separately
(T-ticket-status-assigned-shim-nag), not part of the vocabulary fold.

---

## Removed statuses and their replacements (Akien, 2026-06-15)

| Removed | Why | Do this instead |
|---|---|---|
| **ESCALATED** | *"we don't need escalated. we just bump up the role level."* | Bump the ticket's required role (apprentice→builder→creator→master→guru). |
| **IN_REVIEW** / `needs_review` | *"if I've told you it's sorted, then it's also approved. so we don't need in review."* | `/sorted` = approved. No separate review-wait state. |
| **REJECTED** | *"we don't need rejected we just close them."* | Set CLOSED. |
| **AKIEN** | A pre-approval holding state — now redundant with sorted=approved. | TRIAGE (not ready) or READY (cleared); CLOSED if dropped. |
| **APPROVAL** / `awaiting_approval` | Same redundancy — sorted is the approval. | READY once sorted; TRIAGE until then. |
| **QUESTIONS** / `open_questions` | Folded. | TRIAGE, with the questions as notes in the description. |
| **DESIGN** | Folded. | TRIAGE, with design notes in the description. |
| **PENDING** | Ambiguous catch-all. | TRIAGE (not ready) or DEPENDENCY (blocked on something named). |

---

## Status migration map (old → new)

```
triage            → TRIAGE
design            → TRIAGE
open_questions    → TRIAGE
needs_review      → TRIAGE
pending           → TRIAGE        (or DEPENDENCY if a named blocker exists)
approval          → READY         (sorted = approved)
awaiting_approval → READY
akien             → READY         (or TRIAGE if not yet cleared)
sprint            → READY
in_progress       → INPROGRESS
hold              → HOLD          (unchanged)
dependency        → DEPENDENCY    (unchanged)
escalated         → READY         + role bump (escalation becomes a role change, not a status)
rejected          → CLOSED
done / closed     → CLOSED
```

---

## Rollout plan (sequenced by blast radius)

Done as **three** tickets, not one, because the blast radii differ by an order of
magnitude. Internal status strings are kept lowercase (`triage`, `ready`, …) to
minimize churn; the UPPERCASE names above are the canonical *concept* names.

1. **T-ticket-status-fold-design-triage** — ✅ SHIPPED 2026-06-16.
   Folded `design`, `open_questions`, `needs_review` → `triage` in code AND
   migrated existing DB rows. Conscious claim-path change: `design` was in
   `_ACTIONABLE_STATUSES` (auto-claimable) — dropped, since TRIAGE = not-yet-ready.
   Touched: `cc_queue.py` (STATUS_ORDER, `_ACTIONABLE_STATUSES`, emoji map,
   `_PREFIX_STATUS`, `cmd_migrate_statuses` legacy_map, `cmd_needs_review`,
   docstring), `queue_view.py`, `web_server/server.py`, `uuquestions.py`
   (rewritten — questions are now a description property of TRIAGE tickets, not a
   status), `uushowticket.py`. DB: 1 row (`T-scenario-generator` design→triage),
   snapshot saved, migration logged (AR-009). No cross-store overlap; devlab had 0.

2. **T-ticket-status-approval-akien-removal** (LOW blast radius after the rename
   collapse — see note). Remove `approval` / `akien` / `awaiting_approval` as
   *settable* statuses (sorted = approved, so they're redundant). Only 1 live
   ticket carries one (`T-uc-cert-domain-migration`, status `akien`, pinned by
   `_ID_STATUS_OVERRIDE`); decide its home (recommend DEPENDENCY — blocked on
   Akien's external domain action) — that's an Akien-owned call, so this is filed,
   not executed autonomously.

   **Rename collapse (advisor, 2026-06-16):** the original step 2 was
   `sprint → ready` *string* rename — HIGH blast radius across every `/sprint*`
   skill + dispatch. It collapsed: step 1 kept `sprint` as the internal string and
   put **READY as the display/concept name** (`_STATUS_LABEL` in queue_view +
   web_server already render "Ready"). Per "design for CC's mental model — named
   wrappers over unified core," the internal value need not change. So no string
   rename is planned; READY is `sprint`'s canonical concept name. Revisit only if a
   future need forces the literal value to change.

3. **T-ticket-status-assigned-shim-nag** (NET-NEW behavior).
   Implement ASSIGNED + the 10-minute shim-nag / back-off loop. Does not exist in
   code today; pure addition, designed against the builder shim + Granny dispatch.

## Enforcement
This doc is the source of truth. The status set should be lifted into a single
constant (e.g. `cc_queue.py` `CANONICAL_STATUSES`) that the audit layer can check
against, so a ticket with an off-list status is flagged as drift — the same way
AR-009 enforces interface-crossing logs. (Audit-check ticket is downstream; this
doc is the spec it checks against.)

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

---

## v2 — salience-first DISPLAY taxonomy (Akien, 2026-06-17, commit `da2a658f`)

The canonical *stored* set above is unchanged. v2 is a **display** layer: how open
tickets are grouped and ordered in reports (`uuopentickets`, `uumytickets`,
`queue_view`, the web queue page). Akien ran `uuopentickets`, objected to a bogus
"Pending (legacy → triage/dependency)" group, and specified report groups ordered
by **how little action he needs to take** — least-action first, so reading
top-down he clears the no-action sections first and lands on his action items last:

```
CONSEQUENCE · DEPENDENCY · READY · ASSIGNED · INPROGRESS · TRIAGE · HOLD · AKIEN
```

### Two groups are DERIVED, not stored

- **CONSEQUENCE** (net-new, first section) — "waiting on a date/data we can't
  influence." Identification rule (Akien verbatim): *"every consequence ticket
  starts with `T-consequence-`."* Derived = id-prefix `T-consequence-` **AND an
  uncleared gate**. When the gate clears (date passes / predecessors close) it
  **graduates to its underlying status** — so a passed-date check renders as READY
  and is dispatchable, with **no daily job** (live derivation). This is the answer
  to Akien's "gate passed → auto-move to READY, yes?" — yes, automatically.
  CONSEQUENCE takes precedence over DEPENDENCY (a still-gated check is
  first-and-foremost "waiting", even if its gate is an id).
- **DEPENDENCY** — now **purely derived** (a gated `sprint` ticket = waiting on
  work we could reprioritise). No stored `dependency` status; `cmd_gate` only sets
  the `gate` field. Migrated 21 legacy stored-`dependency` rows → sprint so the
  derivation is the single source.

### Distinctions Akien drew

- **CONSEQUENCE** = can't influence (a date, data becoming available).
- **DEPENDENCY** = could influence by reprioritising another ticket.
- **AKIEN** = needs him to take an external action (spend $). Last group =
  the one action bucket, at the bottom of the report.

### Mechanics

- **`pending` retired:** 13 legacy pending → sprint (the group Akien objected to is
  gone). Legacy tail (`approval`/`escalated`/`design`) stays in `STATUS_ORDER` so a
  stray row is surfaced, never silently dropped (AR-009 spirit).
- **Canonical source:** `effective_status(ticket, all_tickets)` lives in
  `unseen_university/ticket_status.py` (imports `gate_clear` from the `gate_logic`
  leaf). `queue_view.py` + `web_server/server.py` import the SAME object — a
  cross-caller `is`-identity test (`tests/test_ticket_status.py`) pins no-drift.
  `~/bin/uuopentickets` + `uumytickets` keep a hand-maintained local copy (system
  python3 can't import the package); backed up `.bak.20260617`.
- **Follow-up filed:** `T-day-close-gate-sweep` (S, 0.4) — day-close GC of elapsed
  date-gate *strings*. Live derivation is already correct; this only clears the
  lingering cosmetic `[gate: <past-date>]`.
- **Flagged for Akien:** 3 acurite-hardware tickets
  (`T-acurite-usb-isolated-config`, `-integrate-weather-html`, `-isolated-daemon`)
  auto-moved pending→READY but need his physical hardware action — set to AKIEN
  per Akien's ruling 2026-06-17 ("assigned to me cuz I need to find and connect
  the usb ethernet dongle").

### Precedence ladder — set in concrete (Akien, 2026-06-17)

When a ticket can match multiple display groups, this ladder decides:

```
AKIEN > HOLD > CONSEQUENCE > DEPENDENCY
```

Verbatim: *"a dependency on AKIEN trumps all, a dependency on akien HOLD is
next, then CONSIQUENCE, then all other DEPENDANCY."*

Implementation: `effective_status()` checks own-status `akien`/`hold` **first**,
before the consequence/dependency derivation, so a held or Akien-claimed ticket
is never reclassified into a waiting bucket. The top two tiers (gated-on-akien,
gated-on-hold) are currently latent — zero live tickets are gated on an `akien`
or `hold` ticket — so gate-target resolution is not built yet; when such a ticket
exists it will fall into plain DEPENDENCY until it's wired. This note is the
trigger: implement it when the first real instance appears.

**Storage reconciliation (Akien 2026-06-17):** Akien asked whether the `/ticket`
skill could "mark CONSEQUENCE as a status." The answer: the `T-consequence-`
**id prefix IS the durable mark** — no stored status change is needed. The stored
status must remain `sprint` so gate-clearance → READY graduation is free (falls
through `return status` automatically). Storing literal `consequence` as a status
would break graduation: the fallthrough would return `"consequence"` forever,
requiring a daily sweep to un-mark it. The prefix convention achieves the intent
without that debt. Verified: nothing in the codebase programmatically constructs
`T-consequence-` ids (only the constant definition exists); the `/ticket` skill
is already de facto the sole creator.

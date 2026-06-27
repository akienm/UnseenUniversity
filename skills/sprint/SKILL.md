---
name: sprint
description: Claim a ticket, work it, commit, close it. Args: "last", ticket ID, or empty (next in queue).
---

# /sprint — Claim, work, ship

Thin alias over the two canonical primitives: **/query-ticket** picks the work,
**/sprint-ticket** runs it. /sprint owns no logic of its own — selection lives in
/query-ticket, execution in /sprint-ticket. (Compaction is native and
self-managed — no explicit call.)

## Args
- `/sprint last` — sprint the thing just discussed (must be ticketed)
- `/sprint T-xxx` — sprint a specific ticket
- `/sprint` — pick next pending ticket from queue

## Steps

### 1. Select ticket — via /query-ticket (canonical)

`/query-ticket` is the single canonical entry point for "what's next" — never
call `cc_queue.py next`/`list` directly to pick work (CLAUDE.md workflow rule;
it abstracts cc_queue.py today and will switch to the ADC queue device later).

- `/sprint` (no args) → `/query-ticket` surfaces the next available ticket.
- `/sprint T-xxx` → skip selection; that ID is the target.
- `/sprint last` → the most recently discussed ticket (must already be ticketed).

Always sprint from a ticket. When no ticket exists yet, stop here and run
/ticket first — a sprint without a ticket has no place to report done.

### 2. /sprint-ticket \<id\>

Run the full single-ticket execution unit. All build/test/commit/close logic
lives there, including /savestate on close.

## Hard rules
- Always sprint from a ticket — run /ticket first when one doesn't exist.
- Selection is /query-ticket's job; execution is /sprint-ticket's. /sprint
  duplicates neither — it only wires the two together.

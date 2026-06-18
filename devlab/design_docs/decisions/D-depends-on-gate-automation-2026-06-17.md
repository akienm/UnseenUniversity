# D-depends-on-gate-automation-2026-06-17

**title:** Automate ticket dependency ordering — gate field + Granny dispatch eval
**date:** 2026-06-17
**status:** open
**spawned_tickets:** T-skill-capture-depends-on, T-granny-gate-eval, T-consequence-depends-on-gate

## Decision narrative

The `gate` field already supports ticket-ID references (`gate: "T-A"`) and
`gate_logic.gate_clear()` already evaluates multi-predecessor semantics
correctly. Two gaps prevent this from working end-to-end:

1. `/ticket` and `/sorted` skills don't prompt for or write dependency edges at
   filing time — so the graph stays implicit in conversation and never lands on
   the ticket.

2. Granny's dispatch Postgres query (`gate IS NULL OR gate = ''`) skips gated
   tickets entirely rather than evaluating whether their gate has cleared.
   Combined, these mean ticket B never auto-dispatches after ticket A closes —
   someone has to `cc_queue.py ungate` manually.

Fix: (a) update skills to elicit and write `gate: T-xxx` when a ticket obviously
follows another; (b) update Granny's dispatch loop to call `gate_logic.gate_clear()`
on gated tickets and make them eligible when clear.

## Hypothesis

Granny holds ticket B even when a builder is free, releasing it only after A
reaches `closed` — without manual intervention.

## Measurement Signal

Granny dispatch log shows B skipped with reason "gate not clear: T-A not terminal";
then dispatches B on the next cycle after A closes. Validated with a throwaway
ticket pair.

## Goal Link

none: factory-of-factories is the north star vision but no formal G-id filed yet

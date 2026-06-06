# D-hold-gate-enforcement-2026-06-06
**title:** /audit-ticket hold-gate: flag hold-status without named dependency
**date:** 2026-06-06
**status:** open
**spawned_tickets:** T-audit-ticket-hold-gate, T-consequence-hold-gate
**goal_link:** G-process-optimization
**concept_links:** none

## Decision narrative
CC has a history of putting tickets on hold for reasons that don't apply to this project — projected external impact, imagined users, hypothetical downstream components. Holds without a named dependency ticket ID or explicit Akien action are almost always wrong. Adding a structural check to /audit-ticket forces explicit dependency naming at filing time, before the ticket reaches the queue.

## Hypothesis
After T-audit-ticket-hold-gate ships, no hold-status ticket passes /audit-ticket without a named blocking ticket ID or an explicit "Akien:" action in the description.

## Measurement Signal
/audit-ticket returns AMEND for any draft with status=hold and no named dependency; future /sorted batches contain no stale unblocked holds.

## Goal Link
G-process-optimization — structural enforcement of a behavioral rule.

## Concept Links
none

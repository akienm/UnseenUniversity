# D-claim-rename-dispatch-2026-06-13
**title:** Rename claimed_at → dispatched_at; remove ticket-claim language from queue
**date:** 2026-06-13
**status:** open
**spawned_tickets:** T-claim-rename-dispatch, T-consequence-claim-rename

## Decision narrative
LLMs misread `claimed_at` as an autonomous-claim action rather than a Granny dispatch event, causing orphaned in_progress tickets and recurring builder confusion. Rename `claimed_at → dispatched_at` throughout cc_queue.py and referencing files; remove the dead `cmd_claim` stub; update log action strings to use dispatch language. Scope boundary: ticket-dispatch terminology only — action_claim_verifier.py (cognition domain) is explicitly excluded.

## Hypothesis
LLMs reading the queue codebase will correctly interpret the state machine without confusing "dispatched_at" for an autonomous-claim action, reducing the class of orphaned in_progress tickets caused by terminology misreading.

## Measurement Signal
Zero recurrence of "stale claimed ticket" misdiagnoses in CC session logs after rename ships; grep for `claimed_at` in cc_queue.py returns 0 results.

## Goal Link
none: direct friction reduction + factory-of-factories cost savings — same principle as renaming confusing terms for human teammates

# D-sorted-advisor-probe-2026-06-06
**title:** Proactive advisor() call in /sorted for L/XL ticket drafts
**date:** 2026-06-06
**status:** open
**spawned_tickets:** T-sorted-advisor-probe, T-consequence-sorted-advisor
**goal_link:** G-process-optimization
**concept_links:** none

## Decision narrative
/sorted drafts L/XL tickets without advisor review, so completion criteria gaps survive to the queue unchanged. The right moment for Opus judgment is at draft time. D-ticket-quality-verification-loop-2026-06-06 identified this as the cheap high-leverage move: Sonnet drives /sorted, advisor() (Opus) evaluates L/XL drafts before filing. Feedback applied before /audit-ticket runs.

## Hypothesis
L/XL tickets filed after this change have advisor-reviewed completion criteria; sprint reset_count for L/XL tickets decreases over the following 30 days.

## Measurement Signal
L/XL tickets in queue post-change have specific, checkable completion criteria; T-completion-audit-closed-tickets pass rate improves for L/XL tickets; advisor() call visible in /sorted transcript.

## Goal Link
G-process-optimization — proactive Opus judgment at ticket-draft time, not sprint-reset time.

## Concept Links
none

# D-anticipatory-memory-2026-06-04
**title:** Anticipatory memory — proactive pre-brief before sprint + token tracking
**date:** 2026-06-04
**status:** open
**spawned_tickets:** T-sprint-anticipatory-brief, T-token-tracking-per-sprint, T-consequence-anticipatory-memory
**goal_link:** none: token optimization / cost management
**concept_links:** none

## Decision narrative

Current recall is reactive — CC asks, Librarian answers. Anticipatory: when a ticket enters sprint, assemble a briefing from prior tickets on the same files, linked decisions, and any escalation history — before CC reads a single file. Push model, not pull. Token tracking per sprint is the measurement layer: without counting tokens we cannot verify that compiled inference, anticipatory memory, or any other optimization strategy actually reduces consumption. T-token-tracking-per-sprint is the diagnostic instrument; T-sprint-anticipatory-brief is the first optimization it should measure.

## Hypothesis
Sprint token spend decreases after anticipatory brief ships; pre-brief stays under 500-token cap.

## Measurement Signal
sprint_tokens.log before vs. after T-sprint-anticipatory-brief ships; briefing token cost logged at INFO per sprint.

## Goal Link
none: token optimization — not a G-xxx goal but directly supports cost management and compiled inference ethos.

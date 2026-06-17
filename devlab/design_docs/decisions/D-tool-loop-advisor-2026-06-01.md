# D-tool-loop-advisor-2026-06-01
**title:** Redesign ToolLoop with round-based iterations and advisor meta-layer
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-tool-loop-advisor, T-granny-escalation-patterns, T-consequence-tool-loop-advisor
**goal_link:** G-factory-of-factories
**concept_links:** C-prescient-agents-pa20, C-system-availability

## Decision narrative
Current ToolLoop spins to 20 iterations with no course-correction; context explodes (73k tokens for an S ticket). Redesign to round-based structure (5 iter/round, 2 rounds max) with an ADVISOR CALL between rounds. Advisor is a reviewer, not a worker — it returns one of 6 signals: CONTINUE / REPROMPT / UPGRADE / BLOCKED / CONFUSED / ESCALATE. Context resets between rounds. GrannyDaemon adds a PatternTracker (PA2.0 Layer 1→2) aggregating outcome rates by tag/tier/size into a flat JSONL.

## Hypothesis
Workers no longer spin to max iterations; advisor redirects or escalates early; 73k-token context explosions stop.

## Measurement Signal
Average tokens/dispatch drops; REPROMPT/UPGRADE/BLOCKED appear in MINION_RESULT channel alongside ESCALATE; escalation rate by tag is queryable from PatternTracker.

## Goal Link
G-factory-of-factories — advisor protects CC budget (C-system-availability) by preventing runaway loops.

## Concept Links
C-prescient-agents-pa20 — advisor is the Layer 1→2 (observe→discover) feedback mechanism
C-system-availability — advisor prevents budget-burning loops

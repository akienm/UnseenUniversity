# D-minion-model-upgrade-2026-06-01
**title:** Upgrade inference model lineup to coding-benchmark-validated models
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-minion-model-upgrade
**goal_link:** G-factory-of-factories
**concept_links:** C-prescient-agents-pa20

## Decision narrative
Current models (qwen3.5-9b, deepseek-v4-flash) were chosen by price alone and both hit the 20-iteration cap on first live run. Replace with models ranked by SWE-bench Verified and Aider leaderboard: worker→qwen2.5-coder-32b-instruct, analyst→deepseek-v3, designer→gemini-2.0-flash. Minion tier (qwen3.5-9b) stays — cheapest for trivial tasks.

## Hypothesis
Cheap-model workers complete S tickets without hitting the iteration cap; DONE% rises.

## Measurement Signal
MINION_RESULT channel signal=DONE% rises; average iterations/DONE drops below 10 over 20 dispatches.

## Goal Link
G-factory-of-factories — framework builds cheaply so Claude stays available for design.

## Concept Links
C-prescient-agents-pa20 — model upgrades directly serve the factory-of-factories goal by making cheap workers effective for execution

# D-compact-cadence-hook-2026-06-05
**title:** Harness-enforced compaction cadence — Stop hook fires /autocompact every 5 closes; Haiku does the compact
**date:** 2026-06-05
**status:** open
**spawned_tickets:** T-cc-compact-cadence-hook, T-autocompact-haiku-dance, T-consequence-compact-cadence
**goal_link:** none — operational reliability (compaction was silently never firing)
**concept_links:** none

## Decision narrative
Compaction never fired on a cadence for three reasons: (1) the native % auto-compact trigger never arms on Sonnet 1M (live-window occupancy never reaches 85% of 1M); (2) the "every 5 tickets" rule lived only in /sprint-batch, which Granny bypasses by dispatching atomic /sprint-ticket commands; (3) when the compact line is in a skill the model reads, the model treats it as advisory and defers it. Fix: move the trigger out of model-advisory skill text and into a harness-enforced Stop hook registered by ClaudeShim. The hook counts ticket-closes via sprint_tokens.log line count vs a baseline file (external state, shim-owned), and every 5 closes injects /autocompact via tmux — a queued slash command the model cannot defer. /autocompact itself does the Haiku dance (model haiku -> compact -> model sonnet) because Akien prefers Haiku for compaction: cheap, fast, no 1M credits. The root 1M problems are separately removed by CLAUDE_CODE_DISABLE_1M_CONTEXT=1 (pins standard 200K, restores the non-1M picker option, makes /compact work without credits).

## Hypothesis
After this ships, CC compacts automatically every 5 ticket-closes regardless of how the ticket was dispatched, without the model deferring it.

## Measurement Signal
sprint_tokens.log grows by 5 lines -> a /compact event appears in the transcript within the next turn; the session returns to Sonnet afterward.

## Goal Link
none — operational reliability

## Concept Links
none

## Related finding
While reviewing, discovered D-granny-shim-dispatch-handshake-2026-06-04 was a fake completion: daemon.py docstring claims CC.0 dispatch=bus but _default_config() still uses tmux_send_keys, so the handshake never runs for CC. Filed T-granny-bus-flip-fake-completion (hold, guru) under that decision for Akien's judgment.

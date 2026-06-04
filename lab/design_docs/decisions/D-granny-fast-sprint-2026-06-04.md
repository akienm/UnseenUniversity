# D-granny-fast-sprint-2026-06-04
**title:** Granny fast-sprint loop — stale watchdog, dispatched-set prune, CC idle listener, bus flip
**date:** 2026-06-04
**status:** open
**spawned_tickets:** T-granny-stale-inprogress-watchdog, T-granny-dispatched-set-prune, T-cc-worker-idle-listener, T-granny-flip-bus-cc, T-consequence-granny-fast-sprint
**goal_link:** none: sprint-throughput autonomy not yet a named goal
**concept_links:** none

## Decision narrative
Granny was blocking itself: 8 tickets stuck at in_progress prevented _cc0_busy() from clearing; dispatched_cycle.json had 26 stale IDs blocking re-dispatch of reopened tickets; and the bus dispatch handshake (T-granny-bus-dispatch) lacked the CC worker-side IDLE listener needed to close the loop. This decision wires all four missing pieces: a stale-in_progress watchdog (resets after 2h), dispatched-set pruning on load (drops IDs no longer active in DB), a CC IDLE listener (receives bus envelopes, drives ack/started/timeout handshake, injects /sprint-ticket via tmux), and a config flip once the listener is live.

## Hypothesis
After these 4 tickets ship, Granny dispatches the next sprint ticket to CC automatically within one poll cycle (≤60s) of the previous ticket closing, with no stale-ticket accumulation and no manual intervention.

## Measurement Signal
Granny log shows consecutive 'dispatched T-xxx → CC.0' entries after each close; dispatched_cycle.json stays accurate across restarts; no repeated GRANNY_DISPATCH for the same ticket in channel_messages.

## Concept Links
none

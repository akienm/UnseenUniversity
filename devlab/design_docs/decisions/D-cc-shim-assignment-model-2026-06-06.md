# D-cc-shim-assignment-model-2026-06-06
**title:** CC shim owns all delivery; Granny is transport-agnostic bus assignment only
**date:** 2026-06-06
**status:** open
**spawned_tickets:** T-granny-transport-agnostic, T-cc-shim-ack-nag, T-cc-shim-priority-halt, T-consequence-cc-shim-assignment
**goal_link:** none

## Hypothesis
After these tickets ship, Granny daemon contains zero tmux knowledge. A dispatched ticket shows "CC.0 acked at <time>" state. The nag to CC is `\r\r\rcheck messages when possible\n` on a configurable interval. HALT works from any bus device.

## Measurement Signal
grep for 'tmux' in devices/granny/daemon.py returns nothing. Queue show on a dispatched ticket shows acked timestamp. CCWorkerListener test verifies nag fires and HALT fires interrupt sequence.

## Goal Link
none (Granny reliability — precondition for system functioning)

## Decision narrative
Granny makes assignments to the bus mailbox (cc.0). That's all Granny does — she is transport-agnostic. The CC shim (CCWorkerListener) owns: ack receipt (adds note to ticket with timestamp), nag loop (fires `\r\r\rcheck messages when possible\n` via tmux every CC_SHIM_NAG_INTERVAL seconds, default 600), and priority/HALT path (any bus device drops kind=halt/priority envelope → shim fires Enter Enter Enter <message> Enter immediately). tmux knowledge is fully enclosed in the CC device layer.

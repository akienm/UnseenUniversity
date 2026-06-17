# D-ponder-stibbons-device-2026-06-09
**title:** Ponder Stibbons device — human-facing coordinator (Radar O'Reilly equivalent)
**date:** 2026-06-09
**status:** open
**spawned_tickets:** T-ponder-scaffold, T-consequence-ponder-device

## Decision narrative
Ponder Stibbons is the human-facing coordinator of the rack. He is the Radar O'Reilly of UnseenUniversity: personable, anticipates needs, bridges the human to the system without the human needing to understand system internals. He is NOT a leader — he quietly gets things done. He interfaces with HEX (Igor/CC.0 in our case). His fascia will be the natural-language system-state query interface in a future sprint. This sprint only scaffolds the device; the coordinator functionality is a separate design conversation once the routing architecture is clear.

## Hypothesis
A user can ask Ponder a natural-language question about system state and get a human-readable summary without knowing which device owns the answer.

## Measurement Signal
Ponder fascia renders a coherent response to "what's happening?" queries; system-status traffic that currently goes to Igor routes to Ponder instead.

## Goal Link
G-invisible-tools

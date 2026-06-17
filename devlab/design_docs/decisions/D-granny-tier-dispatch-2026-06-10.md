# D-granny-tier-dispatch-2026-06-10
**title:** Granny dispatch: cascading tier pickup + exact-match mode
**date:** 2026-06-10
**status:** open
**spawned_tickets:** T-granny-tier-cascade, T-granny-exact-match-mode

## Decision narrative
Current rules route by exact role match (builder/creator → Dick, master → CC, default → CC). Two missing behaviors:

1. **Cascading pickup (production mode):** A master device should exhaust its own tier first, then absorb creator, then builder if still idle. Idle capacity flows down-tier. Granny needs a per-device `cascade_if_idle: true` flag and a fallback rule that expands the role set when the device's own queue is empty.

2. **Exact-match mode (testing):** For validating a specific agent tier, only route exact role matches. Dick only gets builder, CC only gets master. No bleed. Toggle via granny.yaml or a per-device flag.

3. **Escalation is tier-bump (up), cascade is tier-drop (down):** A Dick failure bumps the ticket role up one tier (builder → creator → master). That's already partially wired via `status: escalated → CC.0`. The gap: escalation should bump role level, not hard-code destination to CC.0 — so an intermediate creator device (if one exists) intercepts before it reaches master.

## Hypothesis
With cascading pickup: CC.0 has zero idle time when there are builder/creator tickets pending. With exact-match: Dick completes only builder tickets; escalated tickets reach CC without intermediate mis-routing.

## Measurement Signal
- Cascade: `SELECT count(*) FROM clan.memories WHERE metadata->>'status'='sprint' AND metadata->>'role' IN ('builder','creator')` drops to 0 before any builder tickets sit idle while CC is free.
- Exact-match: Dick's log shows zero `master`-role ticket attempts.

## Goal Link
System reliability + multi-agent utilization.

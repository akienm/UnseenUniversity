# D-shims-are-listeners-2026-06-18
**title:** Shims are listeners — device shim owns the IDLE loop as a background thread
**date:** 2026-06-18
**status:** open
**spawned_tickets:** T-shim-rack-supervisor, T-ccworker-shim-listener-thread, T-web-device-controls, T-granny-ticket-event-wakeup, T-consequence-shims-are-listeners

## Decision narrative
The shim is everything a device needs to operate and appear in the environment. Instead of spawning cc_worker_listener as a subprocess and babysitting its pid, CCWorkerShim.start() owns the listener loop as a background thread — the shim IS the device's presence. A rack supervisor process (under Ground Loop supervision) holds the shim instances and ticks their watchdogs. The web UI toggles circuit state (flat-file); the supervisor reconciles within one poll. Granny wakes on Postgres LISTEN/NOTIFY instead of sleeping 60s. Together these remove all manual shell steps from the "enable a device" flow: state an intent → it happens.

## Hypothesis
After closing a device's circuit breaker (via web UI toggle or code), the device's shim starts its listener loop as a background thread; the device self-announces to Granny within one poll cycle without any shell commands required.

## Measurement Signal
No cc_worker_listener process in ps aux; shim.self_test() reports 'thread: alive'; Granny channel shows GRANNY_DISPATCH ack within one poll cycle of circuit close; DS.0 visible as 'online' in web UI after toggling. Outcome check: run /outcome when T-consequence-shims-are-listeners gates open (2026-07-02).

## Goal Link
goal: none — factory of factories is an informal north star not yet encoded as a G-xxx palace goal node.

## Context
Root cause was Granny dispatching T-intent-extractor-agent to CC.0 in a loop (5+ times), bypassing availability routing. Fix removed all hardcoded CC.0 routing targets. While auditing, identified that CCWorkerShim used subprocess babysitting — this decision generalizes the fix to the architectural pattern that prevents the same class of issue.

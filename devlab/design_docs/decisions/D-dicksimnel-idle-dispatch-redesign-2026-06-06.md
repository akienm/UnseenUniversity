# D-dicksimnel-idle-dispatch-redesign-2026-06-06
**title:** DickSimnel redesign — pushed dispatch via bus mailbox, not cc_queue polling
**date:** 2026-06-06
**status:** open
**spawned_tickets:** T-dicksimnel-worker-listener, T-dicksimnel-granny-dispatch-wire, T-consequence-dicksimnel-idle-dispatch
**goal_link:** none: DickSimnel architectural parity with CC is a structural quality goal, no G-xxx
**concept_links:** none

## Decision narrative
DickSimnel currently polls cc_queue every 30s asking "do I have work?" The correct pattern (matching how CC works) is a pushed-dispatch listener: Granny sends `{kind: dispatch, ticket_id}` to `dicksimnel.0` mailbox via bus; DickSimnel polls that mailbox (same as CCWorkerListener does for cc.0), receives the envelope, ack+starts the handshake, runs inference, posts result. The device is dormant between dispatches — no active work discovery.

The canonical reference is `devices/granny/cc_worker_listener.py` (CCWorkerListener): it polls `cc.0` mailbox every 5s via `fetch_unseen`, not via `idle_wait`. Granny already has `_dispatch_bus()` and `_process_handshake_replies()` machinery. The only Granny-side change is flipping DickSimnel.0's config from `dispatch: set_worker` to `dispatch: bus, mailbox: dicksimnel.0`.

## Hypothesis
After tickets ship, DickSimnel processes work only when Granny dispatches a ticket envelope; it is dormant between dispatches with no cc_queue.next activity.

## Measurement Signal
DickSimnel logs show no `cc_queue.py next --worker dicksimnel` calls; dispatched tickets flow through ack→started→result cycle; tickets do not stall in 'dispatched' status.

## Key constraint
Deploy order matters: T-dicksimnel-worker-listener must be deployed and the DickSimnel process restarted BEFORE T-dicksimnel-granny-dispatch-wire goes live. Flipping Granny first dispatches to a void — tickets sit in 'dispatched' until _escalate_stale_dispatched fires at 180s.

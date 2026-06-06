# D-granny-shim-dispatch-handshake-2026-06-04
**title:** Two-phase shim-level dispatch handshake to eliminate silent work loss
**date:** 2026-06-04
**status:** open
**spawned_tickets:** T-queue-dispatched-status, T-shim-dispatch-handshake, T-granny-bus-dispatch, T-consequence-granny-handshake
**supersedes:** T-granny-send-keys-idle-check, T-granny-dispatched-set-ttl
**goal_link:** G-process-optimization
**concept_links:** C-registered-dispatcher

## Decision narrative
Granny dispatches work via tmux send-keys, which is silently swallowed when CC is mid-response. Replace with bus envelope dispatch and a two-phase shim handshake: (1) shim acks immediately ("got it") on receive — Granny marks `acked`; (2) shim prods app every 2 min and sends "started" when app picks up — Granny marks `in_progress`; 10-min timeout from ack with no start → shim sends timeout → Granny marks `escalated`. Mechanics live in BaseShim so every worker gets the protocol for free. The specific use case: CC is mid-sprint when Granny sends new work — shim buffers it and delivers when CC is ready, rather than losing it silently.

## Hypothesis
No `dispatched` ticket older than 12 minutes remains unescalated after this ships.

## Measurement Signal
Queue monitoring: `SELECT id, status, updated_at FROM clan.memories WHERE status IN ('dispatched','acked') AND updated_at < now() - interval '12 minutes'` returns 0 rows during normal operation.

## Goal Link
G-process-optimization

## Concept Links
C-registered-dispatcher — Granny is the canonical registered dispatcher; this wires the handshake into the shim layer all dispatchers use.

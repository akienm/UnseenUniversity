# D-bus-postgres-transport-2026-06-10
**title:** Replace IMAP bus transport with Postgres LISTEN/NOTIFY + messages table
**date:** 2026-06-10
**status:** open
**spawned_tickets:** T-bus-postgres-transport, T-consequence-bus-postgres-transport
**supersedes:** T-bus-shim-autostart (closed won't-fix)

## Decision narrative
Replace the IMAP _StubServer/_DovecotClient transport in `bus/` with a Postgres-backed message bus: a `bus.messages` table for persistence and LISTEN/NOTIFY for push delivery. Postgres is already required by the CLAUDE.md rule (Postgres-or-flat-file), so this eliminates the IMAP server as an external dependency with zero new infrastructure. The `bus/` abstraction layer isolates the change — device code (shims, listeners, envelopes) is unchanged.

Origin: conversation about why the IMAP stub server keeps causing operational incidents. The transport analogy (IMAP mailbox = device inbox) still holds; it's only the backing mechanism that changes. LISTEN/NOTIFY gives us IDLE-push semantics; bus.messages gives us persistence, inspection, and routing — all via existing Postgres.

## Hypothesis
Devices exchange envelopes without any IMAP server running; `nc -z 127.0.0.1 10143` exits non-zero while DickSimnel polls successfully.

## Measurement Signal
`SELECT count(*) FROM bus.messages` increments on dispatch; no ConnectionRefusedError from port 10143 in logs; all bus/ tests pass.

## Goal Link
System reliability — eliminate external IMAP server dependency. No G-xxx yet; T-goal-consolidation-review will assign canonical ID.

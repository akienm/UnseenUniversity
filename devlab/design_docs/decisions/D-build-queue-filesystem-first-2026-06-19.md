# D-build-queue-filesystem-first-2026-06-19

**title:** Build queue cuts over to filesystem-first dynamic pipeline; Postgres dropped from ticket path; closed tickets move to a filesystem closed bin
**date:** 2026-06-19
**status:** open
**spawned_tickets:** T-ticket-fs-completeness-audit, T-ticket-store-module, T-cc-queue-fs-first, T-ticket-readers-migrate, T-ticket-pg-drop, T-consequence-build-queue-filesystem-first

## Leading digest

The build queue is the one part of `devlab/runtime/memory/` that is NOT a durable
artifact — it is a **dynamic pipeline**. Tickets flow through it and, once closed,
must leave it (move to `tickets/closed/`) so the active queue dir holds only in-flight
work. Today the queue is inverted: `cc_queue.py` treats Postgres (`clan.memories` +
`devlab.tickets`) as authoritative and the filesystem store as a fail-open mirror
(`cc_queue.py:388` — "The Postgres tables stay authoritative"). That contradicts
CLAUDE.md ("Canonical memory is now the filesystem store") and it is the exact failure
that bit this session: queue work reached for Postgres instead of the filesystem store.
This decision flips it: **filesystem becomes the single source of truth and the queue
is dropped off Postgres entirely.**

## Decision narrative

Akien's framing (verbatim spirit): most of `devlab/runtime/memory/` is durable
artifacts — created-and-remains, referred back to (decisions, sessions). The build
queue is different: 100% dynamic, a pipeline, must not leave artifacts IN THE QUEUE;
closed tickets move to a closed bin. The store moved off the DB because the filesystem
is CC's native mode (CC kept writing decision files anyway), so the queue's native mode
is the filesystem too.

**Two forks Akien resolved (2026-06-19):**
1. **Postgres role → drop entirely.** Not a derived mirror. Tickets live only in the
   filesystem; every ticket-state reader migrates to read JSON files. Cleanest mental
   model; consistent with "we moved everything to your native mode." Bigger blast
   radius (the ~21 readers below) accepted deliberately.
2. **Closed bin → `devlab/runtime/memory/tickets/closed/`** (filesystem subdir of the
   ticket root). Queue dir holds only in-flight; closed tickets move into `closed/`.

**Blast radius — the cutover boundary is every Postgres ticket-state reader, not just
cc_queue.py.** A reader sweep (`grep` for `devlab.tickets` / `TICKETS_ROOT` /
`kind='ticket'`) found ~21 active readers. The dispatch-critical ones must migrate
first or autonomous dispatch silently hangs:
- `devices/granny/daemon.py`, `devices/granny/shim.py`,
  `devices/granny/workflow_executor.py` (`get_ticket_status()` queries `clan.memories`
  → if not migrated, workflow steps never transition to `done` → wave hangs)
- `devices/queue/device.py` (ADC queue rack device — eventual queue home)
- `devices/web_server/server.py` (dashboards)
- `devices/igor/cognition/{action_claim_verifier,dreaming}.py`,
  `devices/scraps/jobs/orphan_watchdog.py`, `devices/librarian/node_registry.py`,
  `unseen_university/devices/librarian/tools/{search_tools,ticket_tools}.py`
- `devlab/claudecode/{stall_check,completion_audit,ticket_detail_eval,uuquestions,
  uushowticket,uuticketadd,cc_nightly_context_prep}.py`
- (migration/archival — leave: `migrate_tickets.py`, `migrate_wg_to_memories.py`,
  `unseen_university/migrations/m_devlab.py`)

**Design shape — one chokepoint.** Introduce a single filesystem **ticket-store module**
(the queue analogue of `memory_emit.py`) that all readers/writers import: `read / list /
write / set_worker / close (→ move to closed/) / next` over `tickets/` + `tickets/closed/`,
using atomic write+rename for concurrent-safety (the same discipline that lets the
filesystem replace Postgres without the SQLite lock-loss failure mode). This prevents 21
scattered JSON-parsing reimplementations (fix-one-leave-many) — readers migrate by
swapping their SQL for one import.

**Sequencing — the queue is LIVE (DS.0 + Granny running an autonomous wave).** No
big-bang flip of the 2630-line file. Incremental, each step reversible:
1. FS-completeness audit (gate) — fail-open projection may have silently dropped tickets;
   verify per-ID parity and backfill before anything trusts the filesystem.
2. Ticket-store module (chokepoint, pure filesystem).
3. cc_queue.py flips to store module; transitional dual-read (FS first, PG fallback)
   until completeness verified.
4. Readers migrate to the store module — dispatch-critical first, then dashboards/search,
   then dev tools.
5. PG dropped last — remove ticket writes + dual-read fallback; verify zero active readers.

## Hypothesis
After cutover, no code path reads or writes ticket state to `clan.memories` or
`devlab.tickets`; the active queue directory holds only in-flight tickets while closed
tickets live in `devlab/runtime/memory/tickets/closed/`; the queue operates correctly
with Postgres stopped.

## Measurement Signal
`grep -rl "clan\.memories\|devlab\.tickets" --include=*.py` over non-migration source
returns zero ticket-state readers; `cc_queue.py list/next/close` succeed with Postgres
down; closed tickets are absent from the active `tickets/` dir and present in
`tickets/closed/`; Granny's autonomous wave continues advancing steps across the cutover.

## Goal Link
none — serves D-filesystem-memory-store-2026-06-16 (completes the queue cutover the
decisions pilot started) and the "build queue is a dynamic pipeline" principle.

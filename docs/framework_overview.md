# Framework Overview

*Migrated from ClaudeAndAkien 2026-04-27. Updated for UnseenUniversity.*

---

## The Core Insight

A Claude Code session is not just a coding assistant — it's a worker with a context window,
tools, and the ability to coordinate with other sessions. UnseenUniversity treats CC sessions
as first-class agents (devices) that share state through the IMAP bus and communicate via
comms:// addresses.

## The Device Model

Every component that connects to the rack is a device:
- It has a mailbox (comms://<device-name>/<mailbox>)
- It registers on startup via the flat-file registry
- It reports health to the rack's rollup loop
- It can subscribe to other devices' mailboxes via IMAP IDLE (pub/sub)

Igor is a device. Claude is a device. Postgres is a device. Each independently deployable.

## The Trail Pattern

All persistent data follows the same shape: a trail through time.

- **Activation trails** — which nodes fired, in what sequence, with what weights
- **Decision logs** — prepend-newest-first; read top until context sufficient
- **IMAP mailboxes** — append-only messages; any subscriber reads via IDLE
- **Logs** — newest at top; cold context at bottom; rarely need the bottom

Trails give you gradients for free: rising heat = active/important, fading heat = deprioritize.

## The Blob Reading Pattern

Any prepend-newest-first log is a "blob" in this framework's terminology.

Reading discipline:
1. Read the slate (what's active — 5-10 lines)
2. Slate points to relevant blobs
3. Read top 40 lines of each blob (newest = most relevant)
4. Stop when context is sufficient

## The Minion Pattern

For code-writing, documentation, migrations, or any focused task:

1. Designer session identifies the work and writes a ticket
2. Ticket is the complete brief — everything the minion needs
3. Minion is spawned (new CC session or Agent tool call)
4. Minion announces on bus, works the ticket, posts result to its mailbox
5. If minion finishes and related work exists: SendMessage to continue
6. Result written to queue IS the record

## Human Checkpoints

Every workflow segment ends at a human touchpoint:
- Plan approval (before L-size implementation)
- Progress check-in (mid-sprint on long tickets)
- Completion review (result posted to bus, human sees it)

Automation works *between* checkpoints, never past them.

## Configuration Discipline

- `.env` — secrets and API keys ONLY
- `DeviceConfig` — per-device policy (queue overflow, restart behavior)
- Flat-file registry — device registration, routing decisions
- Code — behavior; never config values embedded in code

---
name: map
description: "On-demand full-state snapshot of the Igor device (palace, tickets, slates, gates, MCP, logs, processes) → a one-screen summary + a JSON file. Invoked as `/device igor map`. ⚠ Currently non-functional — see the rebuild note."
model: haiku
---

# /device igor map — Full Igor state snapshot

Migrated from the former top-level `/map-igor` into the igor device
(D-skills-two-products / T-device-skills-via-uu-device).

> ⚠ **BROKEN — rebuild is an igor-phase follow-on (igor = last priority).**
> The backing script this skill describes (`${CC_WORKFLOW_TOOLS}/map_igor.py`) does
> **not exist** in the repo, and the section sources below are DB/old-instance-era
> (memory_palace table, decisions_log.dsb, queue.json, Igor-wild-0001) — several no
> longer reflect canonical sources (tickets/decisions are now the filesystem store
> under `devlab/runtime/memory/`, not the DB). This file is migrated to preserve the
> intent and document the gap, not because it runs.
>
> **Rebuild intent:** a zero-inference snapshot belongs in `devices/igor/bin/map`
> (the executor layer), reading CURRENT canonical sources — fs-store tickets/slates/
> decisions, `${IGOR_INSTANCE_ID}` gates/logs, live processes — and emitting a
> one-screen summary + JSON. Until then, `uu device igor state` gives a small,
> correct, working subset.

## Intended usage (once rebuilt)

```bash
uu device igor map                 # full snapshot → one-screen summary + JSON file
uu device igor map --since=yesterday   # diff vs the most recent prior snapshot
uu device igor map --section=tickets   # one section only
```

## Intended sections

palace tree · rules · subsystem index · tickets (fs-store) · slates · decisions
(fs-store) · gates · MCP servers · channels · inbox · logs · DB schema · runtime
tree + processes · code map.

## Hard rules (for the rebuild)

- Read-only — never writes DB, palace, or queue.
- Output file always written; stdout is always the one-screen summary.
- Snapshots auto-expire after 14 days; a file >10MB indicates a collection bug.
- Read canonical sources: the filesystem store for tickets/decisions/slates, not Postgres.

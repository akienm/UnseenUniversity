# agent_datacenter — Claude Code bootstrap

This is the portable agent runtime substrate. It is **not** TheIgors.

---

## What to read first

```bash
# Spec decisions (in TheIgors palace, not this repo):
# memory_get(path="theigors/decisions/D-agent-datacenter-spec-2026-04-27")
# memory_get(path="theigors/decisions/D-adc-phase-0-2026-04-27")  ... phase-5
```

---

## Structural rules

- **device.py** and **shim.py** are the design center. Every component inherits from
  `BaseDevice` / `BaseShim`. OOP-first — no standalone functions doing device work.
- **bus/** owns comms:// routing. Nothing outside bus/ speaks to IMAP directly.
- **skeleton/** owns the MCP aggregator and flat-file registry. No Postgres dependency.
- **devices/** contains one subdirectory per device; each is independently deployable.
- **datacenter_logs/<device>/<subsystem>/** is the log hierarchy. Never write to a flat
  root log file.

---

## Hard rules

- No SQLite. Postgres or flat-file only.
- No TheIgors imports. agent_datacenter must be portable without TheIgors present.
- No live keys or passwords in source. `.env` is gitignored.
- `pip install -e .` must succeed at all times (even with empty stubs).

---

## Phase map (quick orientation)

| Phase | Primary ticket selector | Status |
|---|---|---|
| 0 | `decision:D-adc-phase-0-2026-04-27` | complete |
| 1 | `decision:D-adc-phase-1-2026-04-27` | in-progress |
| 2 | `decision:D-adc-phase-2-2026-04-27` | pending |
| 3 | `decision:D-adc-phase-3-2026-04-27` | pending |
| 4 | `decision:D-adc-phase-4-2026-04-27` | pending |
| 5 | `decision:D-adc-phase-5-2026-04-27` | pending |

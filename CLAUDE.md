# UnseenUniversity — Claude Code bootstrap

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

## Workflow — picking what to work on

**Use `/query-ticket` to ask what's next.** It is the single canonical entry
point for "what should I work on?" — it abstracts cc_queue.py today and will
transparently switch to the ADC queue rack device when that ships.

- `/query-ticket` — read-only, surfaces next ticket, never claims
- `/sprint` (no args) → calls `/query-ticket` logic internally
- Never call `cc_queue.py next` or `cc_queue.py list` directly to pick work

The worker daemon (`worker_daemon.sh`) is **suspended** — no autonomous CC
sprinting until the ADC queue device design is decided.

---

## Hard rules

- No SQLite. Postgres or flat-file only.
- No TheIgors imports. UnseenUniversity must be portable without TheIgors present.
- No live keys or passwords in source. `.env` is gitignored.
- `pip install -e .` must succeed at all times (even with empty stubs).

---

## Phase map (quick orientation)

| Phase | Primary ticket selector | Status |
|---|---|---|
| 0 | `decision:D-adc-phase-0-2026-04-27` | complete |
| 1 | `decision:D-adc-phase-1-2026-04-27` | complete |
| 2 | `decision:D-adc-phase-2-2026-04-27` | complete |
| 3 | `decision:D-adc-phase-3-2026-04-27` | complete |
| 4 | `decision:D-adc-phase-4-2026-04-27` | complete |
| 5 | `decision:D-adc-phase-5-2026-04-27` | partial — discord relocated; cc_mcp_server.py deprecation not yet ticketed |

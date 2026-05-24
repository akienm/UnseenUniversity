# Getting Started

*Migrated from ClaudeAndAkien 2026-04-27. Updated for UnseenUniversity Phase 0.*

UnseenUniversity turns an empty machine into a rack: devices plug in, the IMAP bus
routes messages, and every agent (Claude, Igor, or custom) runs as a first-class device.

---

## Prerequisites

- Python ≥ 3.11
- pip
- Postgres running (local or Docker: `docker run -p 5432:5432 postgres`)

---

## Phase 4+ (target): one-command bootstrap

```bash
pip install UnseenUniversity
agentctl init
```

`agentctl init` will: start the skeleton (MCP aggregator), start the IMAP bus,
find or launch Postgres, register the Postgres device, and print a health summary.

**Status (2026-04-27):** not yet implemented. See `docs/decisions/installer-bootstrap.md`
for the design decision and v0 bridge details.

---

## Phase 0-3 (current): v0 bridge

During Phases 1-3 development, the existing igor launcher is the v0 bridge.
The rack exists and devices can be registered manually. See device README files
under `devices/` for per-device startup instructions as they ship.

---

## Install the package (development)

```bash
git clone https://github.com/akienm/UnseenUniversity
cd UnseenUniversity
pip install -e .
```

---

## Project structure

```
UnseenUniversity/  — core package (rack, device, shim base classes)
bus/               — IMAP bus (comms:// routing, pub/sub)
skeleton/          — MCP aggregator + flat-file device registry
devices/           — one directory per device (postgres, igor, claude, ...)
config/            — DeviceConfig dataclass, per-device policy
logging/           — log hierarchy (datacenter_logs/<device>/<subsystem>/)
tests/             — test suite + fixtures
docs/              — design decisions + workflow guides
```

---

## Key concepts

- **Device**: any component that registers on the rack (BaseDevice / BaseShim contract)
- **Mailbox**: comms://<device-name>/inbox — each device has one, IMAP-backed
- **Skeleton**: MCP aggregator on localhost:port; flat-file registry at startup
- **DeviceConfig**: per-device policy (queue overflow, restart behavior)

See `docs/framework_overview.md` for the full mental model.

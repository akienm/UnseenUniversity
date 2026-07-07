# UnseenUniversity — Architecture

Rack of independently deployable devices on a shared comms:// bus.
Install the platform without any device; install any device without the others.

## Platform

| Layer | Path | Purpose |
|---|---|---|
| Base classes | `UnseenUniversity/device.py`, `UnseenUniversity/shim.py` | `BaseDevice`, `BaseShim` — every device inherits these |
| Bus | `bus/` | comms:// routing, PgBus (Postgres) transport |
| Skeleton | `skeleton/` | flat-file tool registry, no Postgres dependency |
| MCP aggregator | `UnseenUniversity/skeleton/` | MCP server skeleton |

## Devices

| Device | Path | Architecture doc |
|---|---|---|
| Inference | `devices/inference/` | [docs/ARCHITECTURE.md](devices/inference/docs/ARCHITECTURE.md) |
| Librarian | `UnseenUniversity/devices/librarian/` | [docs/ARCHITECTURE.md](UnseenUniversity/devices/librarian/docs/ARCHITECTURE.md) |
| Igor | `devices/igor/` | (pending T-igor-into-adc-device) |
| Claude shim | `devices/claude/` | thin shim only |
| Summarizer | `devices/summarizer/` | thin device |
| Web server | `devices/web_server/` | HTTP frontend |

## Storage

All Postgres via `UU_HOME_DB_URL`:

| Schema | Owner | Contents |
|---|---|---|
| `infra.*` | operational | sessions, channel_messages, balance_history, spend, slates |
| `clan.*` | shared cognition | memories, wg_*, interpretive_edges |
| `adc.*` | project | palace (decisions, goals, hypotheses, questions) |
| `instance.*` | Igor per-instance | ring_memory, twm_observations, pe_chain_priors |

## Key env vars

| Var | Used by |
|---|---|
| `UU_HOME_DB_URL` | all devices that touch Postgres |
| `OPENROUTER_API_KEY` | inference device, budget_gate |
| `OR_BUDGET_ALERT_USD` | budget_gate (default $15) |
| `IGOR_CLOUD_BUDGET_FLOOR_USD` | budget_gate floor gate (default $0) |

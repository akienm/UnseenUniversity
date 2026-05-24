# Akien Device — Architecture

Akien is the human on the rack. This device gives his web and Discord
traffic a `comms://akien/` address that the bus routes like any other device.
No daemon runs — Akien is not a process.

## Key files

| File | Purpose |
|---|---|
| `shim.py` | `AkienShim` — identity, address, data root paths |

## Data home

`~/.unseen_university/akien/` (or `$ADC_RUNTIME_ROOT/akien/`):

| Dir | Purpose |
|---|---|
| `inbox/` | Messages and files delivered to Akien |
| `outbox/` | Files Akien uploads for agents |
| `ideas/` | Akien's notes and goals |

## External interfaces

**Bus address:** `comms://akien/`

**No MCP tools, no HTTP endpoints** — Akien is addressed via the bus and
the web UI chat/upload routes.

**Related:** `T-web-akien-comms` — wires comms://akien/ routing in the web server.

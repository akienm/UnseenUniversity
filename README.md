# UnseenUniversity

Runtime substrate for agent deployments. **Not a framework** — a rack you plug devices into. The cognition is yours; the bus, the registry, and the plug-in contract are here.

Named after the Discworld wizards' university. The devices are named after Discworld characters.

---

## Quick orientation

Two ways to read this document:

- **Installing or building something?** Start at [Getting Started](#getting-started).
- **Trying to understand how it works?** Read [Architecture](#architecture), then see [`docs/TheoryOfOperation.md`](docs/TheoryOfOperation.md) for the full design rationale down to memory node level.

---

## Getting Started

### Prerequisites

- Python ≥ 3.11
- Postgres running locally (`docker run -p 5432:5432 -e POSTGRES_PASSWORD=choose_a_password postgres` works)

### Install

```bash
git clone https://github.com/akienm/UnseenUniversity
cd UnseenUniversity
pip install -e .
```

### Bootstrap a rack

```bash
agentctl init --instance my-first-agent
```

This starts the skeleton (MCP aggregator), starts the IMAP bus, registers the Postgres device, and prints a health summary.

### Plug in an agent

```python
from unseen_university.announce import DatacenterClient, IdentityEnvelope
from unseen_university.bus.imap_server import IMAPServer

server = IMAPServer()
server.start()

identity = IdentityEnvelope(
    agent_id="my-agent",
    instance="my-agent-0001",
    box="my-laptop",
    box_n=0,
    pid=1,
    interface_version="1.0",
    surfaces=["console", "inference"],
)
client = DatacenterClient(identity=identity, imap_server=server)
client.announce()
print("Bound tools:", [t.name for t in client.get_tools()])
```

Expected output: the list of capabilities the broker bound for your agent type. From here, build cognition on top — that's your agent. The rack is the substrate.

### Skill installer (Claude Code users)

```bash
agentctl skills deploy    # push master skills to ~/.claude/skills/
agentctl skills status    # show what's deployed vs master
```
---

## What this is

A portable substrate that any agent can run on:

- **Skeleton** — MCP aggregator on localhost; flat-file device registry; health rollup
- **IMAP bus** — `comms://` addressing; pub/sub via IDLE; 24hr message retention
- **Announce protocol** — agents send an identity envelope, get a manifest of bound capabilities back
- **Device contract** — `BaseDevice` / `BaseShim`; every component is a device
- **Profile system** — declarative YAML per agent type; canonical → runtime; deep-merge inheritance
- **Installer** — agentctl + skill deployer + device manifest

The substrate is reusable across projects. Igor is one tenant; Claude Code is another; your agents can be more.

## What this is NOT

- **Not Igor's cognition.** Igor's narrative engine, working memory (TWM), memory cortex, pe_chain, and engrams live in `devices/igor/` in this repo and run *on* this rack. They are devices like everything else.
- **Not a seed memory corpus.** You bring your own genesis memory. The rack provides the storage device, not its contents.
- **Not a monolith.** Each device is independently runnable, debuggable, and replaceable.

---

## Devices

Every component that connects to the rack is a device. Devices register via the flat-file registry, report health to the rollup loop, and communicate via `comms://` addresses.

### Core infrastructure

| Device | What it does |
|---|---|
| `postgres` | Home DB (clan-shared cross-instance memory, channels, registry) and local DB (per-instance scratch). The only required device. |
| `inference` | LLM inference dispatch. Supports OpenRouter (cloud) and Ollama (local). Igor and other agents route all LLM calls through this device. |
| `web_server` | HTTP dashboard and REST API. Rack status, agent list, channel viewer, and feed endpoints. |
| `sensor` | System telemetry: CPU, memory, disk, SMART. Monitors rack health and surfaces alerts. |

### Agents

| Device | What it does |
|---|---|
| `igor` | The cognition agent. Narrative engine, working-memory workspace (TWM), memory cortex, pe_chain coding workflow, Hebbian engram system. Not a monolith — all subsystems are sub-devices within `devices/igor/`. |
| `claude` | Claude Code session device. Each CC session registers as CC.0, CC.1, etc. The shim bridges the CC tool interface to the bus. |
| `librarian` | Research and retrieval. Answers "what do I know about X?" for all agents. FTS on narrative + tags; optional LLM escalation when nuance is needed. |
| `granny` | Ticket orchestrator. Filing-time audit gate, tag → worker routing via a weighted capability graph (Hebbian: successful routes gain weight), CC dispatch. Named after Granny Weatherwax. |
| `nanny` | Scheduler and world-interaction dispatcher. Cron jobs, periodic tasks, calendar, IoT. Knows what agents exist and which ticket types they handle. Named after Nanny Ogg. |
| `scraps` | Ticket gatekeeper. Rule-based validation before state transitions; optional Qwen 8 fuzzy pass when rules are ambiguous. Script-only — no inference in the critical path. Named after the Igors' dog. |
| `akien` | The human on the rack. Gives Akien's traffic a `comms://akien/` address with inbox, outbox, and ideas mailboxes. Not a daemon; just an addressable presence. |

### Work and data

| Device | What it does |
|---|---|
| `queue` | Work ticket queue served via MCP. Postgres-backed, stateless. `queue_next(worker)` is atomic — reads and marks `in_progress` in a single serializable transaction. No separate claim step. |
| `reader` | Unified URI reader. Accepts `https://`, `calibre://`, `file://`, `blob://`. Caches to blob store; routes to summary or node output mode. |
| `summarizer` | URL or document → tiered output: exec (1-3 sentences), detail (paragraph), chunks (500-word blocks). |
| `workspace` | Workspace management for agent file operations. |

### Communication

| Device | What it does |
|---|---|
| `discord_bot` | Discord integration. Posts to and reads from Discord channels. |
| `browser_use` | Browser automation. Handles web interaction tasks that require a real browser. |
| `swadl` | SWADL testing framework integration stub. |

### Dev and test

| Device | What it does |
|---|---|
| `installer` | Skill deployer (rsync `UnseenUniversity/skills/` → `~/.claude/skills/`) and device manifest manager. |
| `rack_test` | Instrumented test fixture for rack contract testing. Used in tests, not production. |
| `template` | Hello-world starter for building a new device. Copy this to `devices/<your-device>/` and fill in the contract. |

> **Note on tokenization:** Text tokenization (NLP word-splitting for the spreading-activation memory embedding) is not a separate device. It lives in `devices/igor/cognition/word_graph.py` as an internal Igor cognitive utility.

---

## Architecture

### The address hierarchy

```
clan           shared knowledge across all instances of an agent type
  └─ <type>    lineage (e.g. "igor", "cc")
       └─ <id> one running process (e.g. "wild-0001", "cc.0")
            └─ <coa>  center of attention — one stack within an instance
```

Storage in two tiers:

- **`home_db`** — clan-shared Postgres (cross-instance memory, channels, registry)
- **`local_db`** — per-instance scratch (Postgres or flat-file; instance-private)

A **swarm-box** is one machine running one or more instances. Multiple swarm-boxes share the same `home_db`.

### The bus

Every message on the bus is an envelope:

```json
{
  "from":    "comms://igor.wild-0001/inference",
  "to":      "comms://inference.local/cheap-ollama",
  "kind":    "inference.request",
  "payload": { ... },
  "id":      "ulid-...",
  "ts":      "2026-05-02T22:31:00Z"
}
```

The bus routes by `to`. Subscribers IDLE on their mailbox and react. Every envelope persists 24h for durability and replay.

`comms://` addressing:

```
comms://<lineage>.<instance>           primary mailbox
comms://<lineage>.<instance>/console   console surface
comms://<lineage>.<instance>/mcp       MCP surface
comms://<lineage>.<instance>/inference internal inference channel
comms://<channel-name>                 shared / multi-party channel
```

Longest-prefix-wins routing: `comms://cc.0/console` resolves cleanly even when `cc.0` is also registered.

### The announce protocol

How an agent plugs in:

1. Agent constructs an `IdentityEnvelope` (lineage, instance, surfaces, box).
2. Sends it to `comms://announce`.
3. `AnnounceBroker` looks up the agent's profile, builds a `Manifest` (bound tools, channel subscriptions, state refs, ACL), and replies on `comms://announce-events`.
4. Agent caches the manifest. Future tool calls resolve via it.

Full protocol lives in `UnseenUniversity/announce/` with docstrings on each module.

### Two consumer shapes

**Igor-shape — long-running process:**

```python
from unseen_university.announce import DatacenterClient, IdentityEnvelope

client = DatacenterClient(identity=identity, imap_server=imap_server)
client.announce()
binding = client.get_tool("inference")
```

**CC-shape — stateless MCP wrapper:**

```bash
# .mcp.json
{ "mcpServers": { "announce": { "command": "datacenter_mcp" } } }
```

Tools: `announce_tool`, `manifest_tool`, `check_for_invalidate_tool`. CC calls them like any MCP tool; the wrapper holds a singleton `DatacenterClient` underneath.

### Shims

A **shim** is the per-device transport adapter:

```python
from unseen_university.skeleton import BaseShim

class MyShim(BaseShim):
    device_id = "my-device"
    def install(self): ...   # idempotent local setup
    def connect(self): ...   # announce + cache manifest
```

### Profiles

Each agent type carries a YAML profile declaring what it can plug into:

```yaml
# config/profiles/igor.yaml
profile_version: "1.0"
agent_type: igor
allowed_devices:
  - inference
  - postgres
  - browser_use
  - discord_bot
  - web_server
```

Canonical profiles in `UnseenUniversity/config/profiles/`. Runtime copies sync to `~/.unseen_university/profiles/` on install.

---

## What lives where

| Path | What it is |
|---|---|
| `announce/` | Announce protocol — envelopes, broker, client, manifest, listener |
| `bus/` | IMAP server + envelope shape + `comms://` router |
| `skeleton/` | MCP aggregator + flat-file registry + health |
| `cli/` | `agentctl` CLI |
| `skills/` | Master skill set (deployed to `~/.claude/skills/`) |
| `devices/<name>/` | Per-device implementations |
| `devices/igor/` | Igor's full cognition stack (NE, TWM, cortex, pe_chain, tools) |
| `devices/installer/` | Skill installer (manifest + shim + rsync backends) |
| `config/profiles/` | Canonical agent-type profiles (YAML) |
| `docs/` | Architecture docs, getting started, decisions, TheoryOfOperation |

Component-level docstrings at the top of each module are the canonical spec for that component. This README is the assembly map.

---

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 0 | Design locked, repo scaffold | ✅ done |
| 1 | Skeleton + IMAP bus + Postgres device | ✅ done |
| 2 | Igor on the rack | ✅ done |
| 3 | Claude on the rack + YGM | ✅ done |
| 4 | Discord + SWADL + browser-use + installer | ✅ done |
| 5 | Cleanup — retire TheIgors plumbing, unify runtime paths | in progress |

---

## Full design rationale

See [`docs/TheoryOfOperation.md`](docs/TheoryOfOperation.md) for the full architecture outline: why each design decision was made, how the memory system is structured down to node-type level, and how the pieces compose.

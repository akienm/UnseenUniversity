# UnseenUniversity

Runtime substrate for agent deployments. **Not a framework** — a rack you plug
devices into. The cognition is yours; the bus, the registry, and the plug-in
contract are here.

This README is a kit-assembly guide for someone building their own
Igor-shaped agent. The cognition that makes Igor *Igor* is intentionally
not in the kit — that's the body and brain you put on top.

---

## What this is

A portable, dependency-free substrate that any agent can run on:

- **Skeleton** — MCP aggregator on localhost; flat-file device registry; health rollup
- **IMAP bus** — `comms://` addressing; pub/sub via IDLE; 24hr message retention
- **Announce protocol** — agents send an identity envelope, get a manifest of bound capabilities back
- **Device contract** — `BaseDevice` / `BaseShim`; every agent component is a device
- **Profile system** — declarative YAML per agent type; canonical → runtime; deep-merge inheritance
- **Installer** — agentctl + skill deployer + device manifest

The substrate is reusable across projects. Igor is one tenant; CC is another;
your future agents are more.

## What this is NOT

- **Not Igor.** Igor's cognition (NE, TWM, milieu, basal ganglia, engrams,
  reasoning workflow) lives in TheIgors and runs *on* this rack. Don't look
  for cognition here.
- **Not a seed memory corpus.** You build your own genesis; the rack provides
  the storage device, not its contents.
- **Not a monolith.** Each device is independently runnable, debuggable, and
  replaceable.
- **Not TheIgors.** That repo is Akien's research workspace; this is the
  portable substrate underneath.

---

## The hierarchy you'll see in addresses

```
clan         shared knowledge across all instances of an agent type
  └─ <agent-type>      lineage (e.g. "igor", "cc")
       └─ <instance>   one running process (e.g. "wild-0001", "cc.0")
            └─ <coa>   center of attention; one stack within an instance
```

Storage sits in two tiers:

- **`home_db`** — clan-shared Postgres (cross-instance memory, channels, registry)
- **`local_db`** — per-instance scratch (Postgres or flat-file; instance-private)

A **swarm-box** is one physical machine running one or more instances.
Multiple swarm-boxes share the same `home_db`.

---

## The bus shape

### Envelopes

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

The bus routes by `to`. Subscribers IDLE on their own mailbox and react.
Designed for durability and replay — every envelope persists for 24h.

### `comms://` addressing

```
comms://<lineage>.<instance>          primary mailbox
comms://<lineage>.<instance>/console  console surface
comms://<lineage>.<instance>/mcp      MCP surface
comms://<lineage>.<instance>/inference internal inference channel
comms://<channel-name>                shared / multi-party channel
```

The router peels suffix-style addresses with longest-prefix-wins so
`comms://cc.0/console` resolves cleanly even when `cc.0` is also
registered.

### The announce protocol

How an agent plugs in:

1. Agent constructs an `IdentityEnvelope` (lineage, instance, surfaces, box).
2. Agent sends it to `comms://announce`.
3. The `AnnounceBroker` looks up the agent's profile, builds a `Manifest`
   (bound tools, channel subscriptions, state refs, ACL), and replies on
   `comms://announce-events`.
4. Agent caches the manifest. Future tool calls resolve via the manifest
   (`comms://` addresses + permission overlays).

The full protocol — round-trip, error contract, invalidation, IDLE-driven
push — lives in `UnseenUniversity/announce/` with full docstrings on each
module.

---

## Plugging in: two consumer shapes

The same announce protocol serves two kinds of agent.

### Igor-shape: `DatacenterClient`

Long-running Python process. Imports the client directly:

```python
from unseen_university.announce import DatacenterClient, IdentityEnvelope

identity = IdentityEnvelope(
    agent_id="my-agent",
    instance="my-instance-0001",
    box="my-laptop",
    box_n=0,
    pid=os.getpid(),
    interface_version="1.0",
    surfaces=["console", "inference"],
)
client = DatacenterClient(identity=identity, imap_server=imap_server)
client.announce()                           # blocks until manifest arrives
binding = client.get_tool("inference")      # ToolBinding(name, address, ...)
```

Igor wires this in `Igor.__init__` and stashes the client on the cortex so
every cognition path can resolve tool addresses uniformly.

### CC-shape: MCP wrapper

Stateless invocation. Claude Code spawns a subprocess that exposes the
announce protocol as MCP tools:

```bash
# .mcp.json fragment
{
  "mcpServers": {
    "announce": { "command": "datacenter_mcp" }
  }
}
```

Available tools: `announce_tool`, `manifest_tool`, `check_for_invalidate_tool`.
CC calls them like any other MCP tool; the wrapper holds a singleton
`DatacenterClient` under the hood.

---

## Shims and installers

A **shim** is the per-device transport adapter. The base contract:

```python
from unseen_university.skeleton import BaseShim

class MyShim(BaseShim):
    device_id = "my-device"
    def install(self): ...   # idempotent local setup
    def connect(self): ...   # announce + cache manifest
    # Plus capability-reading forwarders pulled from the manifest
```

`agentctl` is the CLI surface:

```bash
agentctl init                   # bootstrap an empty rack on a clean machine
agentctl status                 # rack health from the running skeleton
agentctl skills deploy          # push master skills to ~/.claude/skills/
```

The skill installer pushes a curated set of slash commands from
`UnseenUniversity/skills/` to `~/.claude/skills/` on the local box. The
manifest at `UnseenUniversity/skills/manifest.json` controls what lands
where (machine-agnostic vs lineage-specific; per-host filtering). User-added
local skills not listed in the manifest are never touched.

The `RsyncBackend` (in `devices/installer/backends.py`) is the default
deployment mechanism: idempotent rsync from `UnseenUniversity/skills/<name>/`
to `~/.claude/skills/<name>/` with a manifest-aware allowlist.

---

## Profiles

Each agent type carries a YAML profile that declares what it's allowed to
plug into:

```yaml
# config/profiles/igor.yaml
profile_version: "1.0"
agent_type: igor
description: "Master cognition tenant — full inference, memory, web, browser_use"
inherits: []        # slice 5: deep-merge with __replace__ marker
allowed_devices:
  - inference
  - postgres
  - browser_use
  - swadl
  - discord_bot
  - web_server
```

Canonical profiles live at `UnseenUniversity/config/profiles/<agent-type>.yaml`.
Runtime copies sync to `~/.unseen_university/profiles/<agent-type>.yaml` on
install. Inheritance uses deep-merge; child YAMLs can override individual
keys via the `__replace__` sentinel for explicit list-replacement.

---

## Smoke test — stand up a fresh agent in 5 minutes

Prerequisites: Python ≥ 3.11, Postgres running locally.

```bash
# 1. Install the substrate
pip install -e .

# 2. Bootstrap a fresh rack
agentctl init --instance my-first-agent

# 3. Define a profile (config/profiles/my-first-agent.yaml)
cat > ~/.unseen_university/profiles/my-first-agent.yaml <<'EOF'
profile_version: "1.0"
agent_type: my-first-agent
description: "Smoke test agent"
allowed_devices:
  - inference
EOF

# 4. Plug in via Python
python3 -c "
from unseen_university.announce import DatacenterClient, IdentityEnvelope
from unseen_university.bus.imap_server import IMAPServer

server = IMAPServer()
server.start()
identity = IdentityEnvelope(
    agent_id='my-first-agent',
    instance='my-first-agent-0001',
    box='my-laptop',
    box_n=0,
    pid=1,
    interface_version='1.0',
)
client = DatacenterClient(identity=identity, imap_server=server)
client.announce()
print('Bound tools:', [t.name for t in client.get_tools()])
"
```

Expected output: a list of capabilities the broker has bound for your
agent type. From here, build cognition on top — that's *your* agent; the
rack is just the substrate.

---

## What lives where

| Path | What it is |
|---|---|
| `UnseenUniversity/announce/` | Announce protocol — envelopes, broker, client, manifest, listener |
| `UnseenUniversity/bus/` | IMAP server + envelope shape + comms:// router |
| `UnseenUniversity/skeleton/` | MCP aggregator + flat-file registry + health |
| `UnseenUniversity/cli/` | `agentctl` command-line interface |
| `UnseenUniversity/skills/` | Master skill set (deployed to ~/.claude/skills/) |
| `devices/<name>/` | Per-device implementations (one subdir each) |
| `devices/installer/` | The skill installer (manifest + shim + backends) |
| `config/profiles/` | Canonical agent-type profiles (YAML) |
| `docs/` | Framework overview, getting started, decisions |

The component-level docstrings at the top of each module are the
canonical spec for that component. This README is the assembly map.

---

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 0 | Design locked, repo scaffold | ✅ done |
| 1 | Skeleton + IMAP bus + Postgres device | ✅ done |
| 2 | Igor on the rack | ✅ done |
| 3 | Claude on the rack + YGM | ✅ done |
| 4 | Discord + SWADL + browser-use + installer | ✅ done |
| 5 | Cleanup (superseded TheIgors plumbing) | in progress |

---

## Spec

Full design spec: `D-agent-datacenter-spec-2026-04-27` in TheIgors palace.
Component-level decisions land in `docs/decisions/`.

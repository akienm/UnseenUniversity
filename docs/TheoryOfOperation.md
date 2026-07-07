# Theory of Operation

**Status:** Living document — updated as architecture evolves.
**Implementation status:** This document reflects the target architecture. Sections marked `[Pending: T-xxx]` describe functionality from in-flight tickets expected to ship by 2026-06-02.
**Purpose:** Match the implementation to Akien's mental model. Gaps between this document and the code are candidates for tickets.

This is an outline, not a tutorial. Each section names the piece, states what it does, explains *why* it is the way it is, and points at the code. Where the implementation diverges from this outline, the code wins — update this doc, don't patch the code to match the doc.

---

## 1. The Rack

*I intend that the rack is a composable substrate — an address space, a bus, a registry, and a health layer — so every capability is a pluggable device and the framework never needs to know what's plugged in.*

### 1.1 What it is

UnseenUniversity is a **rack** — a place where devices plug in and communicate. Not a framework you extend; a substrate you build on top of. The rack provides:

- An address space (`comms://` URIs)
- A message bus (MCP to comms:// URI, each device has one)
- A registry (flat-file, survives code crashes)
- A health rollup
- An announce protocol (identity → manifest)

**MCP** MCP is implemented to comms:// devices, and polling and notification is handled by the device shim.

**Why flat-file registry?** The registry must survive a cold start. If the registry lived in Postgres, a DB outage would prevent any device from announcing. Flat files are always readable, even when everything else is down.

### 1.2 Skeleton

`UnseenUniversity/skeleton/` — MCP aggregator + registry + health rollup.

- **MCP aggregator**: exposes all registered device capabilities as a single MCP endpoint on localhost. Claude Code and other MCP consumers connect here; the skeleton routes tool calls to the appropriate device.
- **Flat-file registry**: `~/.unseen_university/registry/` — one JSON file per registered device. Written on announce; deleted on deregister.
- **Health rollup**: polls registered devices for status; aggregates into a single health vector. Exposed via `/api/health`.

### 1.3 Bus

> **Mental model:** The bus *is* the rack's internal network. Every device speaks to every other device through it — nothing communicates out-of-band. A shim is each device's interface to that network: it handles announce, routing, capability advertisement, and wake-on-demand so the device itself never touches transport. The transport is an implementation detail, not the concept: swap it by changing only `bus/` — nothing else moves. (The current transport is **PgBus**, a Postgres-backed bus; an earlier IMAP/Dovecot transport was removed.)

`UnseenUniversity/bus/` — PgBus (Postgres-backed message bus) + `comms://` router + envelope model. Messages persist in the `bus.messages` table; delivery uses Postgres `LISTEN/NOTIFY` with a poll fallback.

Every message is an **envelope** (`bus/envelope.py`):

```json
{
  "from_device":    "comms://igor.wild-0001/inference",
  "to_device":      "comms://inference.local/cheap-ollama",
  "sent_at":        "2026-05-31T00:00:00+00:00",
  "schema_version": "1.0",
  "payload": {
    "kind": "inference.request",
    "...":  "device-specific fields"
  }
}
```

The rigid envelope fields are exactly `from_device`, `to_device`, `sent_at`, `schema_version`, and `payload`. Everything else goes in the open `payload` dict. `kind` is a payload convention, not a rigid field.

**Address resolution:** longest-prefix-wins. `comms://cc.0/console` resolves to the `/console` surface of `cc.0`'s mailbox even when `cc.0` is also registered as a top-level address.

**Receive model:** The bus receive primitives are `PgBus.fetch_unseen(mailbox)` (poll) and `PgBus.idle_wait(mailbox, timeout_s)` (block until a message arrives or the timeout expires, via Postgres `LISTEN/NOTIFY` with a poll fallback). The shim's `start()` launches a bus-facing component (e.g. `AnnounceListener.run_forever`, `HealthAggregator.run_forever`, or a worker poll loop) that drives a loop like:

```python
while not stop.is_set():
    woke = bus.idle_wait(mailbox, timeout_s=25 * 60)
    if woke:
        self.pump()   # fetch_unseen() + process
    # timeout → re-enter (keepalive)
```

Agents never poll their mailboxes directly — the bus-facing component runs the loop; the shim owns that component's lifecycle. `idle_wait` uses Postgres `LISTEN/NOTIFY` and falls back to polling; many worker/builder listeners simply poll `fetch_unseen()` on a fixed interval (default ~5s).

**Request/response:** The bus has no separate RPC mechanism — none is needed. Every envelope carries `from_device`; the responder appends its reply to `to_device=env.from_device`, and the requester's poll/`idle_wait` delivers it. The announce → manifest flow is the canonical working example:

1. Agent appends an `IdentityEnvelope` to `comms://announce` (its own mailbox as `from_device`)
2. `AnnounceListener` receives via `idle_wait`, resolves the profile, appends a Manifest reply back to `env.from_device`
3. Agent's IDLE loop wakes; `fetch_unseen()` returns the manifest

When a device needs the reply to go to a *different* address than `from_device`, the convention is to include `reply_to` in the payload. This is a payload convention, not a rigid envelope field.

---

## 2. Devices

*I intend that every device exposes a uniform lifecycle and observability contract so the rack can manage, restart, and inspect any device without knowing its internals.*

### 2.1 The device contract

Every device inherits from `BaseDevice` (in `unseen_university/device.py`). The abstract interface has 15 methods in four groups:

```python
class BaseDevice(ABC):
    device_id: str          # unique rack address prefix (from DEVICE_ID or class name)

    # Identity
    def who_am_i(self) -> dict: ...        # required keys: device_id, name, version
    def interface_version(self) -> str: ... # INTERFACE_VERSION this device was built against

    # Capability & routing
    def requirements(self) -> dict: ...    # required key: deps (list[str])
    def capabilities(self) -> dict: ...    # can_send, can_receive, emitted_keywords
    def comms(self) -> dict: ...           # comms:// address, mode, push/pull/nudge flags
    def where_and_how(self) -> dict: ...   # host, pid, launch_command

    # Observability
    def health(self) -> dict: ...          # status: healthy|degraded|unhealthy, detail, checked_at
    def uptime(self) -> float: ...         # seconds since start
    def startup_errors(self) -> list: ...  # errors from most recent startup
    def logs(self) -> dict: ...            # subsystem → log path
    def update_info(self) -> dict: ...     # current_version, update_available

    # Lifecycle control
    def restart(self) -> None: ...         # graceful restart
    def block(self, reason: str) -> None: ... # suppress auto-relaunch
    def halt(self) -> None: ...            # immediate stop
    def recovery(self) -> None: ...        # attempt recovery from degraded state
```

`start`, `stop`, and `self_test` are **not** part of the abstract interface and do not exist in `BaseDevice`. Lifecycle is controlled via `restart`/`halt`/`recovery`/`block`.

**Why OOP-first?** A single well-known entry point per device makes lifecycle management (`restart`/`halt`/`recovery`) and observability (`health`/`uptime`/`logs`) uniform. The framework can iterate all devices — restart, drain, upgrade — without knowing their internals.

A **shim** (`BaseShim`) is each device's interface to the bus network. It handles the announce protocol, wraps capabilities as MCP tools, and owns the device's lifecycle on the rack. The device focuses on its domain logic; the shim makes it easy for the device to talk to everything else. See §1.3 for the bus mental model.

### 2.2 Device directory

See the [device table in README.md](../README.md#devices) for the current full list. Key groupings:

- **Core infrastructure**: `postgres`, `inference`, `web_server`, `sensor`
- **Agents**: `igor`, `claude`, `librarian`, `granny`, `nanny`, `scraps`, `akien`
- **Work/data**: `queue`, `reader`, `summarizer`, `workspace`
- **Communication**: `discord_bot`, `browser_use`, `swadl`
- **Dev/test**: `installer`, `rack_test`, `template`

**Why one directory per device?** Blast-radius containment. A broken import in one device cannot crash the whole rack on startup. Each device is independently deployable, testable, and replaceable.

### 2.3 Security Tiers

The rack uses a two-tier execution model. Tier determines the security perimeter, not the capability surface.

**Tier 1 — Trusted agents (Igor, CC, Librarian, Granny, Nanny)**

Enforcement: policy gate (`devices/policy/gate.py`) + path sandbox. All tool calls route through `BaseShim.dispatch()`, which traces every call to `~/.unseen_university/logs/shim/trace/YYYYMMDD.jsonl`. The policy gate runs before each dispatch and allows/denies based on the agent's manifest and the calling context.

These agents run as host processes. The rack trusts them at the same level as any same-UID process on the machine — because they are same-UID processes. Prompt injection is the in-scope threat; process escape is not.

**Tier 2 — Untrusted / external agents (ContainerShim)**

Enforcement: the container boundary IS the security perimeter. `ContainerShim` (`unseen_university/skeleton/container_shim.py`) wraps the agent process in a Docker container with:

- `--network=none`: the container has no TCP stack at all. It cannot reach the host's Postgres, the message bus, or any other network service directly. Bridge networking is explicitly rejected — a bridge gateway exposes host services to containers even when those services bind to `0.0.0.0`.
- **Unix domain socket only**: the shim binds a socket at `HOST_DIR/uu-shim-<device_id>.sock` and mounts it into the container at `/var/run/uu-shim.sock`. This is the only channel in or out.
- **docker.sock denied**: mounting `/var/run/docker.sock` grants full Docker API access and trivially escapes the container. `ContainerShim.start()` raises `ValueError` on any mount containing `docker.sock`, regardless of the profile.
- **Resource limits**: `--cpus` and `--memory` from the profile's `container.resource_limits` block.

Container spec is per-agent-profile (see `config/profiles/external_agent.yaml` for the reference). Existing tier-1 profiles are not affected — `container:` block is optional and ignored by tier-1 shims.

**Lifecycle wiring**: `ContainerShim.start()` launches the container; `ContainerShim.stop()` halts it. The rack caller (typically the announce listener or a supervisor daemon) is responsible for invoking these on announce and deregister respectively. The skeleton's `register_device()` / `deregister_device()` manage the flat-file registry but do not call shim lifecycle methods — shim start/stop is the caller's responsibility at all tiers.

**Why Docker, not a VM?** Docker provides process-level isolation sufficient for the threat model (prompt-injected agents; not physically-local adversaries). VMs are explicitly out of scope for v1. See `docs/security_known_limitations.md` for the full list of accepted gaps.

**Why not enforce at the network layer for tier-1?** Same-UID same-machine trusted processes communicate at the process level. Inter-process network inspection would require deep packet interception and would add latency with no real security gain for processes that already share the OS context. The policy gate (bus message routing) is the correct enforcement layer for tier-1.

### 2.4 Logging Convention

Every device must log **state changes** and **interface crossings**.

**State changes** include: ticket status transitions, device lifecycle events (start/stop/restart/halt), routing decisions, auth/trust events. **Interface crossings** include: channel post/read, DB write/read, subprocess spawn, MCP tool dispatch, and calls across device/shim boundaries.

Log levels:
- `INFO` — each interface crossing and significant state transition
- `DEBUG` — high-frequency state changes (Hebbian edge weight updates, internal counter increments)
- `WARNING` — recoverable degradation (channel post failed, fallback activated)
- `ERROR` — unrecoverable within the current operation (binary not found, subprocess spawn failed)

**Why?** Without a log at a crossing point, a bug at a device boundary is invisible — you cannot tell whether the problem is in the sender or the receiver, or whether the message crossed at all. Logs at crossings make `/diagnose` effective: given a ticket ID, the log chain shows exactly when it moved between devices and which device lost it.

**Enforcement:** audit check AR-009 (`audit_check_interface_logging.py`) flags `BaseDevice` methods that invoke interface-crossing primitives (`subprocess.Popen/run/call`, `post_to_channel`) without a log call in the same method body. The check detects the "no log at all" case; the implementation target is INFO on success and WARNING on failure for each crossing.

**Reference implementation:** `devices/granny/device.py` — `_post_to_channel`, `_dispatch_to_cc`, `register_worker`, `strengthen_edge`, `weaken_edge` all log their crossings and state changes.

---

## 3. Platform Subsystems

*I intend that each platform subsystem is a separately deployable device with a narrow, replaceable responsibility — so any subsystem can be upgraded or swapped without touching its callers.*

### 3.1 Queue device

`devices/queue/` — Postgres-backed, stateless. `queue_next(worker)` is a single serializable transaction: read next eligible ticket → mark `in_progress` → return. No claim step; dispatch IS assignment.

**Why no claim?** A separate claim step creates a window where a ticket is claimed but not started, requiring a timeout/reset mechanism. Atomic dispatch eliminates that window.

### 3.2 Granny Weatherwax — orchestrator

`devices/granny/` — The ticket gateway. Responsibilities:

1. **Filing-time audit gate**: checks required fields, valid size, description sections. Tickets that fail the gate don't enter the sprint queue.
2. **Routing**: tag → worker via a weighted capability graph. Each `(tag, worker_id)` pair is a `RoutingEdge` with a weight. Successful dispatches strengthen the edge (Hebbian learning). Unknown tags escalate to CC.
3. **Dispatch**: for CC tickets, posts `GRANNY_DISPATCH|ticket=T-xxx|...` to the shared channel.

**CC is the catch-all worker.** `_ticket_needs_cc` returns `True` for everything except `minion`-tagged tickets. The `_CC_TAGS` set is intentionally broad, but the operative logic is tag-based: only an explicit `minion` tag routes to cheap inference workers (OR deepseek/qwen). Igor-assigned tickets are treated as CC-bound — Igor coding was retired in 2026-05 (all sprint tickets go to CC). Any ticket without a recognized routing tag escalates to CC rather than to cheap inference. The inversion is intentional: the safe default is the capable worker, not the cheap one.

### 3.3 cc_task_listener

`devlab/claudecode/cc_task_listener.py` — Polls the shared channel for `GRANNY_DISPATCH` messages and calls `cc_queue.py dispatch` to mark tickets `in_progress`. Runs as a background thread inside `GrannyDaemon`.

**Why a listener instead of direct dispatch?** CC doesn't have a persistent inbound channel. The channel is the handoff point; the listener bridges the bus to CC's queue.

### 3.4 Memory Cortex

`devices/igor/memory/cortex.py` and `clan.memories` in Postgres.

Long-term memory. Each memory node:

| Field | What it is |
|---|---|
| `id` | yyyymmddhhmmss123456 < memory ID used to link back to from other memories
| `name` | Unique string key (often human-readable: `PR_GOAL_ASPIRATIONAL_SUCK_LESS`) |
| `narrative` | The content — what Igor knows or believes |
| `memory_type` | See §3.5 |
| `metadata` | JSON blob — type-specific fields (goal_type, status, code_ref, etc.) |
| `parent_id` | Forms a tree; child nodes inherit context from parents |
| `inertia` | 0–1; how resistant to change (high = load-bearing, touch carefully) |
| `activations` | How many times this node fired in recent NE cycles |
| `confidence` | 0–1; how certain Igor is this is true |
| `portable` | Whether this node migrates across instances |

Memory lives in `clan.memories` — shared across all Igor instances. Instance-private scratch lives in `instance.*` tables.

### 3.5 Memory Node Types

| Type | What it stores | Examples |
|---|---|---|
| `FACTUAL` | Things Igor believes to be true | Architecture rules, codebase knowledge, world facts |
| `GOAL` | Something Igor is working toward | `GOAL_STANDING_READ_WEB`, `GOAL_STANDING_ASK_AKIEN_QUESTIONS` |
| `PROCEDURAL` | A scheduled habit with a `code_ref` tool call | `PROC_GOAL_CONTINUATION`, `PROC_BOREDOM_TRIGGER` |
| `INTERPRETIVE` | A pattern-match / rule of thumb | "When X, usually Y" |
| `EPISODIC` | A specific event that happened | "On 2026-05-29 I diagnosed a routing bug in Granny" |
| `REFERENCE` | A pointer or frame node | `PR_GOAL_ASPIRATIONAL_SUCK_LESS` (background frame) |
| `EXPERIENTIAL` | Runtime observations, metric signals, and emotional states captured during operation | Routing speed measurements, emotional nodes, growth/learning moments |
| `CREDENTIAL_REF` | Pointer to a credential location (not the credential itself) | "Anthropic API key — in .env ANTHROPIC_API_KEY" |
| `IDENTITY` | Self-model facts about Igor's own nature and operating principles | "I am a memory network with spreading activation retrieval", "Every revision must reduce friction" |
| `CORE_PATTERN` | Deep axioms and first-principles that underpin Igor's reasoning | "The world is not a safe place", "There's always a why", "Make everything suck less" |
| `ROLE_MODEL` | Named entities in Igor's relational world — people, AI partners, cultural references | Akien (creator), Claude (reasoning partner), Igor (Discworld, cultural model) |
| `ROOT` | Singleton instance-anchor node — the identity root for one Igor instance | `ROOT`: "I am Igor-wild-0001. I learn, I remember, I explain my reasoning." |

`PROCEDURAL` nodes are the scheduled habit system. Each has a `code_ref` in metadata pointing to a registered tool function, and a `schedule_interval_sec` that the `SchedulerSource` fires on a timer.

**Why memory types instead of a flat store?** The NE applies different spreading-activation weights by type. PROCEDURAL nodes trigger tool calls rather than NE reasoning. FACTUAL nodes have inertia; high-inertia nodes require CC pre-approval before editing. Type separates "what kind of thing this is" from "what the content says."

**Versioning:** any node type can act as a version facia — the head of an append-only version chain. REFERENCE nodes most commonly play this role. See §3.7 for the full facia pattern.

### 3.6 Two-Tier Semantic Search

The rack uses two distinct semantic search mechanisms, by design (D-shared-memory-service-2026-05-28):

**Tier 1 — Igor's word graph** (`devices/igor/cognition/word_graph.py`):

A weighted undirected graph where nodes are words/bigrams and edges encode co-occurrence strength. Igor's *primary* retrieval path:

- `tokenize(text)` → seed nodes in the graph
- Spreading activation propagates outward through co-occurrence edges
- `L2-normalize` produces a semantic vector — no external API required
- Used every NE cycle for memory arc selection; fast, local, offline-capable

**Tier 2 — Rack embedding engine** (`devices/scraps/embedding_engine.py`):

OpenAI `text-embedding-3-small` (1536-dim) with a hash-based fallback (384-dim, fully deterministic). The rack's *shared* semantic embedder:

- **Igor's second pass**: provides a higher-fidelity vector check when spreading activation is ambiguous
- **Everyone else's first pass**: CC, Librarian, and other agents use this directly — they don't have a word graph
- Called at **write time** by `devices/librarian/memory_writer.py` — embeddings are pre-computed and stored alongside memories in `payloads JSONB`
- Called at **query time** by `devices/librarian/recall.py` — cosine similarity against stored embeddings for "what do I know about X?" queries
- The word graph is also compared against these embeddings as a training signal (`_log_wg_comparison`)

**Design note**: The embedding engine lives inside `devices/scraps/` alongside the ticket validator. It's a platform service — any agent (Igor, Librarian, future agents) calls it via `embed()` and `embed_batch()`. The organizational home (Scraps vs. standalone device vs. Librarian-owned) is a choice, not a debt; the current arrangement keeps it co-located with the one device that already calls it frequently (rule-based ticket validation), and other agents reach it as a library function.

**The full retrieval stack** (Librarian recall):
1. FTS on `narrative` text (free, instant)
2. Tag overlap scoring
3. Vector similarity via pre-computed embeddings (cosine distance, no inference at query time)
4. Optional LLM escalation for nuance (writes result back so next recall is cheaper)

### 3.7 Versioning — the facia pattern

Every versioned artifact in the memory graph has a **facia** node: the node that currently represents the head of its version chain. The facia is the default resolution target when the rack looks up a versioned artifact by key.

Version chains are **append-only**:

1. Write the new version as a new memory node.
2. Set a `version_of` edge from the new node to the old facia.
3. Update the key to resolve to the new node — it is now the facia.
4. The prior facia is now a **tail**: still in the graph, still readable, no longer the head.

No existing node is rewritten or deleted. The tail chain *is* the audit trail.

**Example — an interpretive rule gets a new version:**

```
[RULE_V2]   ← facia (current — rack resolves to this)
  │ version_of
[RULE_V1]   ← tail (prior version)
  │ version_of
[RULE_V0]   ← tail (original)
```

To get the current version: look up the key → facia. To read history: traverse `version_of` edges from the facia toward the tail.

**Applies to:** `clan.memories` nodes, agent manifests, eval rubrics, policy rules, factory specs, and agent profiles. Any artifact that can change over time follows this pattern.

**Why not in-place update?** In-place rewrites destroy the audit trail silently. A facia redirect preserves every prior version without a separate log. The graph structure is the archive.

The append-only model recurs across the system: new state appends, nothing is erased, history is a traversal, not a special query.

REFERENCE nodes most commonly carry facia semantics — they are "pointer or frame" nodes by design (see §3.5 type table). Other node types (GOAL, INTERPRETIVE, FACTUAL) can also head a version chain when the versioned artifact is of that type.

### 3.8 Agent Taxonomy [Pending: T-agent-taxonomy-concept]

Agents on the rack are classified into three classes. The classification affects routing, capability dispatch, and memory access policy.

| Class | Description | Examples | Memory access | Persistence |
|---|---|---|---|---|
| `utility` | Bounded, composable, no persistent state | Scraps, Google Secretary | None — stateless | No; per-call only |
| `specialized` | Domain expert; owns a memory slice; long-lived | Granny, Nanny, Librarian, Vetinari | Owns a slice; others request access via channel | Yes |
| `general-purpose` | Broad reasoning; full memory access | Igor, CC | Full access to all tiers (see §5.4) | Yes |

The `agent_class` field is added to each device's registry manifest (`skeleton/registry.py`). The skeleton's capability routing reads `agent_class` when resolving tool dispatch and memory access requests.

**Why classify?** Without a taxonomy, every routing and access-control decision is ad-hoc — "does this agent need memory?" is answered differently by every caller. The class label makes the decision explicit and measurable. A `utility` agent that tries to write memory is a contract violation, not an ambiguity.

### 3.9 Archivist — Compiled Inference Proxy [Pending: T-archivist-device]

`devices/archivist/` — Sits between every caller and the LLM. Intercepts inference calls rack-wide.

**Two paths:**

1. **Graph hit**: the Archivist's knowledge graph can answer the query. No LLM call is made. Answer goes directly to the caller.
2. **Graph miss**: the LLM runs. Answer goes to the caller immediately. A learning payload also fans out to the overnight pipeline: Librarian edge maintenance → graph update. The miss that just happened compiles into a cheaper hit for next time.

**Knowledge graph properties:**
- Purely epistemic — observations are recorded as fact, not emotionally encoded. This distinguishes the Archivist's graph from Igor's graph trees, which carry emotional encoding as a load-bearing feature of his cognition. Do not conflate them.
- Append-only, facia-versioned (see §3.7).

**Librarian relationship:** Librarian remains the knowledge retrieval/research service. Archivist owns the inference proxy layer and the overnight learning pipeline. The two are separate devices with separate responsibilities.

**Bootstrap:** All historical conversation logs can be fed through the learning engine in chunks to pre-populate the graph before the first live inference call. The graph does not start empty.

**Economics:** Graph hit rate rises over time as the graph grows. LLM call rate falls. The system gets cheaper per inference call as it learns. This is the rack-level implementation of compiled inference applied to inference itself — the same principle the system uses everywhere else, turned inward.

**Why?** Every inference call that misses has a chance to compile into a graph lookup. Without this layer, the cost of inference is flat; with it, the marginal cost per call trends toward zero for the queries the system has seen before. The economics improve automatically.

---

### 3.10 Registered Dispatcher Pattern (C-registered-dispatcher)

A recurring shape in the rack: a **queue of work items** + a **registry of capable handlers** + **dispatch rules** — and nothing else. Granny is the canonical implementation; the pattern recurs across devices and is the target shape for new devices.

**The three stacks:**

| Stack | What it holds | Granny example |
|---|---|---|
| Work queue | Items waiting to be handled | `cc_queue.py` sprint tickets |
| Handler registry | Handlers that have self-declared their capabilities | `GrannyWeatherwaxDevice._workers` dict |
| Routing rules | How to match a work item to a handler | `get_workers_for_role(role)` + `is_available()` |

**The key invariant:** handlers self-declare capabilities at startup. The dispatcher does not know about specific workers at build time; it only knows how to match. Adding a new handler type requires no changes to the dispatcher — only the handler needs to call `register_worker(roles=[...])`.

**Two variants:**

1. **Capability matching** — handler declares a set of roles/tags it accepts. The dispatcher does a set intersection: which handlers accept this work item? Picks the first available one. Used by Granny for role-based ticket dispatch.

   ```python
   workers = device.get_workers_for_role(role)
   available = [w for w in workers if w.is_available()]
   if available:
       available[0].dispatch_fn(ticket)
   ```

2. **Sequential filter pipeline** (Chain of Responsibility) — an ordered sequence of handlers, each attempting the work and passing to the next on failure. Used by Granny's OR cascade (analyst → worker → minion) and by Inference's multi-tier escalation.

   ```python
   for tier in (analyst, worker, minion):
       result = tier.run(ticket)
       if result != ESCALATE:
           return result
   ```

**When to use capability matching vs. filter pipeline:**
- Capability matching: handlers are equally capable for a role, availability is the tiebreaker. Adding a second CC instance (CC.1) requires zero dispatcher changes.
- Filter pipeline: handlers are tiered by cost or power — try the cheapest/fastest first, escalate only when it can't handle the item. The pipeline encodes a quality/cost trade-off.

**Device fit inventory:**

| Device | Pattern variant | Status |
|---|---|---|
| Granny (ticket dispatch) | Capability matching | ✓ canonical — implemented T-granny-dispatch-role-map |
| Granny (OR cascade) | Filter pipeline | ✓ implemented — analyst → worker → minion tiers |
| Inference | Filter pipeline | partial — tiers are hardcoded, not self-registered |
| GoogleSecretary | neither — bespoke | refactor candidate (T-google-secretary-registered-dispatcher) |
| Librarian | neither — direct tool call | refactor candidate if routing grows |

**Why this pattern and not a bespoke orchestrator?** A bespoke orchestrator encodes routing in code — adding a worker means editing the dispatcher. The Registered Dispatcher encodes it in data: the handler's declaration. The dispatcher becomes infrastructure rather than a feature. New agents join the rack by self-registering; no existing code changes.

**Concept node:** `palace.concepts.registered-dispatcher` (C-registered-dispatcher)

---

## 4. Igor — Reference Implementation

*I intend that Igor demonstrates what it looks like to build a cognition-bearing agent on the rack using the same bus, queue, and memory abstractions available to every other device — not a special case, a reference implementation.*

Igor is a device (`devices/igor/`), the reference implementation of an agent built on the rack. His cognition subsystems use the platform abstractions defined in §1–3: the bus (§1.3), the queue (§3.1–3.3), and memory (§3.4–3.7). They run on the rack like everything else; they are not special.

### 4.1 Narrative Engine (NE)

`devices/igor/cognition/narrative_engine.py`

The NE is the main loop of Igor's cognition. Each **NE cycle**:

1. Reads the working-memory workspace (TWM) for active observations.
2. Spreads activation across the memory graph via the word graph.
3. Selects a narrative arc (the most-activated path through memory).
4. Calls the LLM with the arc as context.
5. The LLM produces one of: `ACTION_IMPULSE`, `NARRATIVE_GAP`, `REFLECTION`, `NO_ACTION`.
6. The result is deposited back into TWM.

**Why a narrative arc, not a prompt?** Spreading activation surfaces what's most relevant to the current context without requiring the LLM to search memory explicitly. The arc is the retrieved context; the LLM does reasoning, not retrieval.

### 4.2 Working-Memory Workspace (TWM)

`devices/igor/memory/twm.py` and `instance.twm_observations` in Postgres.

TWM is a short-lived, high-salience workspace. Each observation has:

- `content_csb` — the content in key|value format
- `salience` — 0–1; higher = more likely to surface to NE
- `expires_at` — observations decay; TWM is not permanent storage
- `category` — `observation`, `goal`, `maintenance`, etc.
- `thread_id` — groups related observations into a reasoning thread

**Why not just use the LLM's context window?** Context windows are wiped each turn. TWM persists across turns, survives crashes (Postgres-backed), and can be inspected externally. It is Igor's "what's on my mind right now" that outlasts any single inference call.

TWM observations are append-only and never rewritten in place; see §3.7 for the facia/versioning pattern.

### 4.3 pe_chain (Plan → Execute chain)

`devices/igor/tools/pe_chain.py`

The coding workflow. When Igor is assigned a ticket, pe_chain steps through phases:

```
INIT → CLAIM → READ → PLAN → FILTER → SITUATE →
OBSERVE → STORE_OBSERVE_RESULTS → HYPOTHESIZE → IMPLEMENT → TEST → PROBE → CLOSE
```

Each phase writes to a `basket` (a dict in TWM) and reads from it. The basket is the shared state that survives crashes mid-chain.

**Why a named phase chain?** Each phase can be stepped manually for debugging. A stuck HYPOTHESIZE phase is diagnosable without re-running the whole chain. The basket in TWM means a restarted Igor can resume mid-chain rather than starting over.

The basket follows the same append-only pattern; see §3.7.

### 4.4 Engrams

`clan.memories WHERE memory_type='PROCEDURAL'`

Engrams are compiled habits. Each is a PROCEDURAL memory node with:
- `code_ref`: `"namespace:tool_name"` — the function to call when this habit fires
- `schedule_interval_sec`: how often SchedulerSource fires it (absent = manual-only)

The SchedulerSource in `devices/igor/cognition/push_sources.py` reads all PROCEDURAL memories with `schedule_interval_sec` and fires their `code_ref` tools on a timer.

**Why habits in Postgres, not code?** Habits can be added, removed, or retimed at runtime without a code deploy. Igor can learn new habits; CC can seed new habits via `psql`. The habit system is data-driven.

Engrams follow the same append-only/facia pattern; see §3.7.

---

## 5. Storage Architecture

*I intend that storage is organized into tiers by scope — clan-shared, per-instance, flat-file, and client-private — so the right data lives at the right isolation level by default and the privacy boundary is structural, not conventional.*

### 5.1 clan.* (cross-instance)

Tables shared across all Igor instances and CC instances:

| Table | Content |
|---|---|
| `clan.memories` | Long-term memory: all node types |
| `clan.interpretive_edges` | Edge weights between memory nodes |
| `channel_messages` | Shared channel (CC ↔ Igor ↔ Granny ↔ others) |
| `adc.palace` | Project knowledge, decisions, goals, day rollups |

**Clan template and memory domain ownership [Pending: T-igor-clan-template]:** Igor is an instance of a clan template. The template carries baseline memories that every Igor instance starts with; per-instance memories layer on top and do not affect other instances. Memory domain ownership is assigned per device: Granny owns all memories related to builds and routing; Vetinari owns task-management and project-status memories. A device that owns a memory domain is the authoritative writer for that domain — other devices read, but do not write, without requesting access via the channel.

### 5.2 instance.* (per-instance)

Tables private to one running Igor instance:

| Table | Content |
|---|---|
| `instance.twm_observations` | Working-memory workspace (short-lived) |
| `instance.ring_memory` | Recent SAID/HEARD ring buffer |
| `instance.tails` | Reasoning trails (activation sequences) |

### 5.3 Flat files

Runtime state that must survive a DB outage:

| Path | Content |
|---|---|
| `~/.unseen_university/registry/` | Device registry (one JSON per device) |
| `~/.unseen_university/<instance>/igor.cfg` | Instance config (IGOR_INSTANCE_ID, DB URL, etc.) |
| `~/.unseen_university/<instance>/igor.switches.cfg` | Safety gates (never in DB — see §6) |
| `~/.unseen_university/claudecode/<date>.slate.txt` | CC daily work slate |

### 5.4 Memory Scope Tiers [Pending: T-memory-scope-layers]

Memory is organized into four tiers with separate Postgres DBs per tier. Tiers differ in ownership, access, and contribution model.

| Tier | Scope | Storage | Owner | Access |
|---|---|---|---|---|
| **Global** | Universal patterns; any UU deployment | Forkable git repo; cloned at `uu bootstrap` | Community (PRs) | Read by all; write via PR |
| **Local/Instance** | This deployment's shared working memory | `clan.memories` (current) | Rack-wide | All agents on this bus |
| **Agent** | Per-device owned memory | Per-device Postgres schema | The device | Device owns; others request via channel |
| **Client** | Per-human private memory | Separate Postgres DB per client | The human (Akien, Leah, etc.) | Client and explicitly granted agents |

**Why separate tiers?** Tier separation prevents leakage in both directions. Client private data cannot bleed into global patterns. Global patterns cannot be polluted by deployment-specific state. The tiers make the privacy boundary explicit and enforced by DB separation, not convention.

### 5.5 Global Knowledge Base [Pending: T-global-kb-git-repo]

The global tier (§5.4) is stored as a git repository. New UU instances clone it at bootstrap via `uu bootstrap`; the local working copy is the seed for the Local tier.

**Contribution flow:** when the Archivist's overnight pipeline identifies a locally-proven pattern that generalizes, it proposes it via `uu contrib submit`. The proposal creates a PR against the upstream global KB repo. Community review decides whether the pattern is universal enough to merge. Merged patterns become available to every UU deployment on their next `uu bootstrap --update`.

**Content constraints:** the global KB is purely epistemic. No deployment-specific state, no personal data, no emotionally-encoded content. Only patterns that have proven useful in a local deployment and have been judged universal go in.

**Why git?** Contribution, review, rollback, and history are all built-in. A bad merge can be reverted. A contribution's provenance is always visible. PRs are a well-understood review mechanism that works across organizations without requiring a shared access model.

---

## 6. Safety Architecture

*I intend that the safety perimeter is structurally inaccessible to Igor's own cognition — filesystem-only gates and cycle counters that no prompt injection or runaway loop can disable from the inside.*

### 6.1 Safety gates

Three named switches live only in `igor.switches.cfg` on the filesystem:

- `IGOR_TIER5_ENABLED` — direct Anthropic API access
- `IGOR_ARBITER_ENABLED` — human-approval queue
- `IGOR_SELF_EDIT_ENABLED` — source write capability

**Why filesystem-only?** Any memory node with `cortex.store()` access could flip a DB-stored gate. Filesystem gates cannot be set by Igor's own cognition — only a human editing the file. This is the primary self-modification safety perimeter.

### 6.2 Alignment reviews

The NE tracks consecutive cycles without human interaction. After 5 cycles on any goal, it posts an alignment review request to the channel. The human replies to reset the counter.

**Why 5 cycles?** Long enough that normal autonomous work doesn't generate noise; short enough that a runaway loop surfaces within minutes.

### 6.3 Safe mode

`IGOR_SAFE_MODE=true` in `igor.switches.cfg` halts pe_chain execution. The watchdog sets it after 30 consecutive stuck NE cycles. Human clears it manually.

---

## 7. Claude Code on the Rack

*I intend that CC is a first-class rack device — registered, lifecycle-managed, and skill-driven — so its behavior is compiled and deterministic rather than ad-hoc and conversational.*

### 7.1 CC as a device

`devices/claude/` — CC registers on the rack like any other device. The shim handles the announce protocol. Multiple CC instances can be active simultaneously: CC.0 (main), CC.1, CC.2 (minions).

### 7.2 Skills

`UnseenUniversity/skills/` — master skill set. Each skill is a directory with a `SKILL.md` (the compiled procedure) and optional `run` script.

The installer (`devices/installer/`) deploys skills from `skills/` to `~/.claude/skills/` via rsync. Manifest-controlled: machine-specific skills are filtered by hostname; user-added local skills are never overwritten.

Skills are **compiled inference** — multi-step workflows encoded as structured instructions that Claude Code executes deterministically. `/sprint`, `/day-close`, `/savestate`, `/diagnose` are all skills.

### 7.3 Hooks

Hooks in `~/.claude/settings.json` run on every matching tool call regardless of context state. Key hooks:

- `UserPromptSubmit` — checks the YGM (you've got mail) mailbox; fires before every CC turn
- `PostToolUse` — formatter hooks (black, etc.) run after every file edit
- `Stop` / `StopFailure` — session logging, usage tracking

---

## 8. Alignment: Where to Look for Gaps

*I intend that this document is a falsifiable alignment artifact — every section can be checked against the running system, and every gap found here becomes a ticket.*

This document is also an alignment artifact. Review each section against the code and ask:

1. **Does the code match the "why"?** If a design rule exists for a stated reason but the implementation no longer follows the rule, that's a ticket.
2. **Are there components not listed here?** Absent components are invisible to the mental model.
3. **Do the memory node types match what's actually in the DB?** Run: `SELECT memory_type, count(*) FROM clan.memories GROUP BY memory_type` and compare to §3.5.
4. **Are the safety gates actually filesystem-only?** Run: `SELECT id FROM clan.memories WHERE id IN ('SYSCFG_IGOR_TIER5_ENABLED', 'SYSCFG_IGOR_ARBITER_ENABLED', 'SYSCFG_IGOR_SELF_EDIT_ENABLED')` — should return 0 rows.
5. **Does the BaseDevice contract exist in practice?** Verify `start`, `stop`, `health`, `self_test` appear in at least 3 concrete device classes in `devices/`.
6. **Is the log hierarchy respected?** Check that `~/.unseen_university/logs/` contains only `<device>/<stream>/` paths (stream ∈ info|warn|debug) — no flat files or mystery device names.

Gaps found in this review become tickets. That is the intended use of this document.

### Last run: 2026-05-30

**Check 3 — memory node types** (vs §3.5):

```
 memory_type   | count
----------------+-------
 EPISODIC       | 70363
 FACTUAL        | 44849
 INTERPRETIVE   |  3101
 GOAL           |  2933
 EXPERIENTIAL   |  2560
 REFERENCE      |   638
 PROCEDURAL     |   504
 CREDENTIAL_REF |   167
 IDENTITY       |    30
 CORE_PATTERN   |    13
 ROLE_MODEL     |     9
 ROOT           |     5
(12 rows)
```

**RESOLVED (T-theory-doc-memory-types):** §3.5 previously documented 6 types; DB has 12. The six absent types — `EXPERIENTIAL`, `CREDENTIAL_REF`, `IDENTITY`, `CORE_PATTERN`, `ROLE_MODEL`, `ROOT` — were added to the §3.5 table on 2026-05-30. Doc and DB are now aligned.

---

**Check 4 — safety gates in DB** (should return 0 rows):

```
 id
----
(0 rows)
```

**RESOLVED (T-theory-safety-gates-in-db):** Three stale nodes (`SYSCFG_IGOR_ARBITER_ENABLED`, `SYSCFG_IGOR_SELF_EDIT_ENABLED`, `SYSCFG_IGOR_TIER5_ENABLED`) were present from before the `T-safety-gates-above-env-sync` filter was added to `env_sync.py`. They were pre-fix artifacts written by the old `push_vars_to_graph` before `SAFETY_GATE_NAMES` exclusion was in place. The hydration code was already refusing them (defense-in-depth). The nodes were deleted from `clan.memories` on 2026-05-30; DB state and doc are now consistent.

---

**Check 5 — BaseDevice contract in practice:**

Inspected `unseen_university/device.py`: the abstract base defines `health`, `restart`, `halt`, `recovery` — but does **not** declare `start`, `stop`, or `self_test` as abstract methods. Across 5 sampled device classes: `health` is universal; `start`/`stop` appear only in `discord_bot`; `self_test` appears only in `granny`. The 4-method contract shown in §2.1 is aspirational, not enforced by the base class.

**GAP:** §2.1 contract snippet (`start/stop/health/self_test`) does not match the actual 15-method abstract interface. Lifecycle uniformity is not enforced. Filed: T-theory-basedevice-contract-mismatch.

---

**Check 6 — log hierarchy:**

`~/.unseen_university/logs/` contains: `queue/info/` ✓, `librarian/curation.jsonl` ✗ (flat file, no stream dir), `d/info/` ✗ (single-char device name — test debris), `_minimaldevice/info/` ✗ (test scaffold debris), `unknown/info/` ✗ (fallback name — a device ran without `DEVICE_ID` set). (Layout reconciled to `<device>/<stream>/` 2026-06-25, T-per-device-log-hierarchy; pre-reconciliation debris used the old `<device>/log/json/` layout under a cwd-relative `datacenter_logs/`.)

**GAP:** `librarian` bypasses the subsystem level; three mystery device directories indicate test debris or missing `DEVICE_ID`. Filed: T-theory-log-hierarchy-anomalies.

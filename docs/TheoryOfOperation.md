# Theory of Operation

**Status:** Living document — updated as architecture evolves.
**Purpose:** Match the implementation to Akien's mental model. Gaps between this document and the code are candidates for tickets.

This is an outline, not a tutorial. Each section names the piece, states what it does, explains *why* it is the way it is, and points at the code. Where the implementation diverges from this outline, the code wins — update this doc, don't patch the code to match the doc.

---

## 1. The Rack

### 1.1 What it is

UnseenUniversity is a **rack** — a place where devices plug in and communicate. Not a framework you extend; a substrate you build on top of. The rack provides:

- An address space (`comms://` URIs)
- A message bus (IMAP)
- A registry (flat-file, survives code crashes)
- A health rollup
- An announce protocol (identity → manifest)

**Why IMAP?** IMAP is a durable, append-only store with a well-tested IDLE push mechanism. Every message persists for 24h, giving replay capability without a separate event store. IMAP servers are widely available and require no bespoke infrastructure. The tradeoff: higher latency than a socket bus, acceptable because agent cognition cycles are measured in seconds, not milliseconds.

**Why flat-file registry?** The registry must survive a cold start. If the registry lived in Postgres, a DB outage would prevent any device from announcing. Flat files are always readable, even when everything else is down.

### 1.2 Skeleton

`UnseenUniversity/skeleton/` — MCP aggregator + registry + health rollup.

- **MCP aggregator**: exposes all registered device capabilities as a single MCP endpoint on localhost. Claude Code and other MCP consumers connect here; the skeleton routes tool calls to the appropriate device.
- **Flat-file registry**: `~/.unseen_university/registry/` — one JSON file per registered device. Written on announce; deleted on deregister.
- **Health rollup**: polls registered devices for status; aggregates into a single health vector. Exposed via `/api/health`.

### 1.3 Bus

`UnseenUniversity/bus/` — IMAP server + `comms://` router + envelope model.

Every message is an **envelope**:

```json
{
  "from":    "comms://igor.wild-0001/inference",
  "to":      "comms://inference.local/cheap-ollama",
  "kind":    "inference.request",
  "payload": { ... },
  "id":      "ulid-...",
  "ts":      "ISO-8601"
}
```

Address resolution: longest-prefix-wins. `comms://cc.0/console` resolves to the `/console` surface of `cc.0`'s mailbox even when `cc.0` is also registered as a top-level address.

Pub/sub: subscribers IDLE on their own mailbox. The bus delivers by appending to the target mailbox; the IDLE connection wakes the subscriber.

---

## 2. Devices

### 2.1 The device contract

Every device inherits from `BaseDevice` (in `unseen_university/device.py`). The contract:

```python
class BaseDevice:
    device_id: str          # unique rack address prefix
    def start(self): ...    # idempotent — safe to call if already running
    def stop(self): ...     # clean shutdown
    def health(self): ...   # returns DeviceHealth(status, detail)
    def self_test(self): ... # smoke test; called by agentctl status
```

**Why OOP-first?** A single `start/stop/health/self_test` entry point per device makes lifecycle management uniform. The framework can iterate all devices — restart, drain, upgrade — without knowing their internals.

A **shim** (`BaseShim`) is the transport adapter. It handles the announce protocol and wraps the device's capabilities as MCP tools. The device itself is transport-agnostic.

### 2.2 Device directory

See the [device table in README.md](../README.md#devices) for the current full list. Key groupings:

- **Core infrastructure**: `postgres`, `inference`, `web_server`, `sensor`
- **Agents**: `igor`, `claude`, `librarian`, `granny`, `nanny`, `scraps`, `akien`
- **Work/data**: `queue`, `reader`, `summarizer`, `workspace`
- **Communication**: `discord_bot`, `browser_use`, `swadl`
- **Dev/test**: `installer`, `rack_test`, `template`

**Why one directory per device?** Blast-radius containment. A broken import in one device cannot crash the whole rack on startup. Each device is independently deployable, testable, and replaceable.

---

## 3. Igor's Cognition Stack

Igor is a device (`devices/igor/`). His cognition subsystems are sub-devices within that directory. They run on the rack like everything else; they are not special.

### 3.1 Narrative Engine (NE)

`devices/igor/cognition/narrative_engine.py`

The NE is the main loop of Igor's cognition. Each **NE cycle**:

1. Reads the working-memory workspace (TWM) for active observations.
2. Spreads activation across the memory graph via the word graph.
3. Selects a narrative arc (the most-activated path through memory).
4. Calls the LLM with the arc as context.
5. The LLM produces one of: `ACTION_IMPULSE`, `NARRATIVE_GAP`, `REFLECTION`, `NO_ACTION`.
6. The result is deposited back into TWM.

**Why a narrative arc, not a prompt?** Spreading activation surfaces what's most relevant to the current context without requiring the LLM to search memory explicitly. The arc is the retrieved context; the LLM does reasoning, not retrieval.

### 3.2 Working-Memory Workspace (TWM)

`devices/igor/memory/twm.py` and `instance.twm_observations` in Postgres.

TWM is a short-lived, high-salience workspace. Each observation has:

- `content_csb` — the content in key|value format
- `salience` — 0–1; higher = more likely to surface to NE
- `expires_at` — observations decay; TWM is not permanent storage
- `category` — `observation`, `goal`, `maintenance`, etc.
- `thread_id` — groups related observations into a reasoning thread

**Why not just use the LLM's context window?** Context windows are wiped each turn. TWM persists across turns, survives crashes (Postgres-backed), and can be inspected externally. It is Igor's "what's on my mind right now" that outlasts any single inference call.

### 3.3 Memory Cortex

`devices/igor/memory/cortex.py` and `clan.memories` in Postgres.

Long-term memory. Each memory node:

| Field | What it is |
|---|---|
| `id` | Unique string key (often human-readable: `PR_GOAL_ASPIRATIONAL_SUCK_LESS`) |
| `narrative` | The content — what Igor knows or believes |
| `memory_type` | See §3.4 |
| `metadata` | JSON blob — type-specific fields (goal_type, status, code_ref, etc.) |
| `parent_id` | Forms a tree; child nodes inherit context from parents |
| `inertia` | 0–1; how resistant to change (high = load-bearing, touch carefully) |
| `activations` | How many times this node fired in recent NE cycles |
| `confidence` | 0–1; how certain Igor is this is true |
| `portable` | Whether this node migrates across instances |

Memory lives in `clan.memories` — shared across all Igor instances. Instance-private scratch lives in `instance.*` tables.

### 3.4 Memory Node Types

| Type | What it stores | Examples |
|---|---|---|
| `FACTUAL` | Things Igor believes to be true | Architecture rules, codebase knowledge, world facts |
| `GOAL` | Something Igor is working toward | `GOAL_STANDING_READ_WEB`, `GOAL_STANDING_ASK_AKIEN_QUESTIONS` |
| `PROCEDURAL` | A scheduled habit with a `code_ref` tool call | `PROC_GOAL_CONTINUATION`, `PROC_BOREDOM_TRIGGER` |
| `INTERPRETIVE` | A pattern-match / rule of thumb | "When X, usually Y" |
| `EPISODIC` | A specific event that happened | "On 2026-05-29 I diagnosed a routing bug in Granny" |
| `REFERENCE` | A pointer or frame node | `PR_GOAL_ASPIRATIONAL_SUCK_LESS` (background frame) |

`PROCEDURAL` nodes are the scheduled habit system. Each has a `code_ref` in metadata pointing to a registered tool function, and a `schedule_interval_sec` that the `SchedulerSource` fires on a timer.

**Why memory types instead of a flat store?** The NE applies different spreading-activation weights by type. PROCEDURAL nodes trigger tool calls rather than NE reasoning. FACTUAL nodes have inertia; high-inertia nodes require CC pre-approval before editing. Type separates "what kind of thing this is" from "what the content says."

### 3.5 Two-Tier Semantic Search

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

### 3.6 pe_chain (Plan → Execute chain)

`devices/igor/tools/pe_chain.py`

The coding workflow. When Igor is assigned a ticket, pe_chain steps through phases:

```
INIT → CLAIM → READ → PLAN → FILTER → SITUATE →
OBSERVE → STORE_OBSERVE_RESULTS → HYPOTHESIZE → IMPLEMENT → TEST → PROBE → CLOSE
```

Each phase writes to a `basket` (a dict in TWM) and reads from it. The basket is the shared state that survives crashes mid-chain.

**Why a named phase chain?** Each phase can be stepped manually for debugging. A stuck HYPOTHESIZE phase is diagnosable without re-running the whole chain. The basket in TWM means a restarted Igor can resume mid-chain rather than starting over.

### 3.7 Engrams

`clan.memories WHERE memory_type='PROCEDURAL'`

Engrams are compiled habits. Each is a PROCEDURAL memory node with:
- `code_ref`: `"namespace:tool_name"` — the function to call when this habit fires
- `schedule_interval_sec`: how often SchedulerSource fires it (absent = manual-only)

The SchedulerSource in `devices/igor/cognition/push_sources.py` reads all PROCEDURAL memories with `schedule_interval_sec` and fires their `code_ref` tools on a timer.

**Why habits in Postgres, not code?** Habits can be added, removed, or retimed at runtime without a code deploy. Igor can learn new habits; CC can seed new habits via `psql`. The habit system is data-driven.

---

## 4. The Queue and Dispatch Chain

### 4.1 Queue device

`devices/queue/` — Postgres-backed, stateless. `queue_next(worker)` is a single serializable transaction: read next eligible ticket → mark `in_progress` → return. No claim step; dispatch IS assignment.

**Why no claim?** A separate claim step creates a window where a ticket is claimed but not started, requiring a timeout/reset mechanism. Atomic dispatch eliminates that window.

### 4.2 Granny Weatherwax — orchestrator

`devices/granny/` — The ticket gateway. Responsibilities:

1. **Filing-time audit gate**: checks required fields, valid size, description sections. Tickets that fail the gate don't enter the sprint queue.
2. **Routing**: tag → worker via a weighted capability graph. Each `(tag, worker_id)` pair is a `RoutingEdge` with a weight. Successful dispatches strengthen the edge (Hebbian learning). Unknown tags escalate to CC.
3. **Dispatch**: for CC tickets, posts `GRANNY_DISPATCH|ticket=T-xxx|...` to the shared channel.

### 4.3 cc_task_listener

`lab/claudecode/cc_task_listener.py` — Polls the shared channel for `GRANNY_DISPATCH` messages and calls `cc_queue.py dispatch` to mark tickets `in_progress`. Runs as a background thread inside `GrannyDaemon`.

**Why a listener instead of direct dispatch?** CC doesn't have a persistent inbound channel. The channel is the handoff point; the listener bridges the bus to CC's queue.

---

## 5. Storage Architecture

### 5.1 clan.* (cross-instance)

Tables shared across all Igor instances and CC instances:

| Table | Content |
|---|---|
| `clan.memories` | Long-term memory: all node types |
| `clan.interpretive_edges` | Edge weights between memory nodes |
| `channel_messages` | Shared channel (CC ↔ Igor ↔ Granny ↔ others) |
| `adc.palace` | Project knowledge, decisions, goals, day rollups |

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

---

## 6. Safety Architecture

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

### 7.1 CC as a device

`devices/claude/` — CC registers on the rack like any other device. The shim handles the announce protocol. Multiple CC instances can be active simultaneously: CC.0 (main), CC.1, CC.2 (minions).

### 7.2 Skills

`UnseenUniversity/skills/` — master skill set. Each skill is a directory with a `SKILL.md` (the compiled procedure) and optional `run` script.

The installer (`devices/installer/`) deploys skills from `skills/` to `~/.claude/skills/` via rsync. Manifest-controlled: machine-specific skills are filtered by hostname; user-added local skills are never overwritten.

Skills are **compiled inference** — multi-step workflows encoded as structured instructions that Claude Code executes deterministically. `/sprint`, `/day-close`, `/savestate`, `/diagnose` are all skills.

### 7.3 Hooks

Hooks in `~/.claude/settings.json` run on every matching tool call regardless of context state. Key hooks:

- `UserPromptSubmit` — checks YGM (you've got mail) inbox and IMAP; fires before every CC turn
- `PostToolUse` — formatter hooks (black, etc.) run after every file edit
- `Stop` / `StopFailure` — session logging, usage tracking

---

## 8. Alignment: Where to Look for Gaps

This document is also an alignment artifact. Review each section against the code and ask:

1. **Does the code match the "why"?** If a design rule exists for a stated reason but the implementation no longer follows the rule, that's a ticket.
2. **Are there components not listed here?** Absent components are invisible to the mental model.
3. **Do the memory node types match what's actually in the DB?** Run: `SELECT memory_type, count(*) FROM clan.memories GROUP BY memory_type` and compare to §3.4.
4. **Are the safety gates actually filesystem-only?** Run: `SELECT id FROM clan.memories WHERE id IN ('SYSCFG_IGOR_TIER5_ENABLED', 'SYSCFG_IGOR_ARBITER_ENABLED', 'SYSCFG_IGOR_SELF_EDIT_ENABLED')` — should return 0 rows.
5. **Does the BaseDevice contract exist in practice?** Verify `start`, `stop`, `health`, `self_test` appear in at least 3 concrete device classes in `devices/`.
6. **Is the log hierarchy respected?** Check that `datacenter_logs/` contains only `<device>/<subsystem>/` paths — no flat files or mystery device names.

Gaps found in this review become tickets. That is the intended use of this document.

### Last run: 2026-05-30

**Check 3 — memory node types** (vs §3.4):

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

**GAP:** §3.4 documents 6 types; DB has 12. Six types are absent from the doc: `EXPERIENTIAL` (2,560 nodes), `CREDENTIAL_REF` (167), `IDENTITY` (30), `CORE_PATTERN` (13), `ROLE_MODEL` (9), `ROOT` (5). Filed: T-theory-doc-memory-types.

---

**Check 4 — safety gates in DB** (should return 0 rows):

```
              id
-------------------------------
 SYSCFG_IGOR_ARBITER_ENABLED
 SYSCFG_IGOR_SELF_EDIT_ENABLED
 SYSCFG_IGOR_TIER5_ENABLED
(3 rows)
```

**GAP:** Doc claims these IDs do not exist in `clan.memories`; all 3 are present. Safety gate state is stored in the DB, contradicting the "filesystem-only" claim. Filed: T-theory-safety-gates-in-db.

---

**Check 5 — BaseDevice contract in practice:**

Inspected `unseen_university/device.py`: the abstract base defines `health`, `restart`, `halt`, `recovery` — but does **not** declare `start`, `stop`, or `self_test` as abstract methods. Across 5 sampled device classes: `health` is universal; `start`/`stop` appear only in `discord_bot`; `self_test` appears only in `granny`. The 4-method contract shown in §2.1 is aspirational, not enforced by the base class.

**GAP:** §2.1 contract snippet (`start/stop/health/self_test`) does not match the actual 15-method abstract interface. Lifecycle uniformity is not enforced. Filed: T-theory-basedevice-contract-mismatch.

---

**Check 6 — log hierarchy:**

`datacenter_logs/` contains: `queue/log/json/` ✓, `librarian/curation.jsonl` ✗ (flat file, no subsystem dir), `d/log/json/` ✗ (single-char device name — test debris), `_minimaldevice/log/json/` ✗ (test scaffold debris), `unknown/log/json/` ✗ (fallback name — a device ran without `DEVICE_ID` set).

**GAP:** `librarian` bypasses the subsystem level; three mystery device directories indicate test debris or missing `DEVICE_ID`. Filed: T-theory-log-hierarchy-anomalies.

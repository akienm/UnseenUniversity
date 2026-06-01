# D-memory-scope-layers-2026-06-01
**title:** 4-tier memory scope (global/local/agent/client) + git-distributed global KB
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-memory-scope-layers, T-global-kb-git-repo, T-archivist-global-contrib, T-clan-instance-scoping, T-consequence-memory-scope
**goal_link:** G-uu-platform, G-factory-factory
**concept_links:** C-compiled-inference, C-agent-taxonomy

## Decision narrative
Memory is organized into 4 tiers with separate Postgres databases: Global (universal patterns, read by all, governance-controlled writes — distributed as a forkable git repo), Local/Instance (this deployment's shared working memory, read by non-blocked local agents), Agent (per-device owned store, others request via bus), Client (per-human private DB, owner-controlled). The global tier is a standalone git repo — new UU instances clone it at bootstrap; community members contribute via PRs; domain forks carry their own patterns and contribute generic ones upstream. The Archivist proposes locally-proven patterns to the global repo after human review.

## Hypothesis
A new UU instance bootstraps from the global KB git repo and has baseline patterns available immediately; client DBs contain no cross-client data; agents accessing other agents' data go via bus request, not direct query.

## Measurement Signal
`uu bootstrap` succeeds from a fresh clone; global KB repo exists on GitHub; client DB schema has no cross-client foreign keys.

## Goal Link
G-uu-platform — portable erector set where any deployment bootstraps from shared patterns.
G-factory-factory — domain forks of the global KB are the product; the methodology generalizes.

## Concept Links
C-compiled-inference — global KB is the compiled inference commons.
C-agent-taxonomy — per-agent memory ownership maps to agent class (utility/specialized/general).

---

## Tier specification

### Connection map

| Tier | Factory | Env var | Postgres schema | Separate DB instance? | Status |
|------|---------|---------|----------------|-----------------------|--------|
| Global KB | `make_global_proxy()` | `UU_GLOBAL_KB_DB_URL` | `global,public` | Yes (standalone global KB DB) | Not yet provisioned |
| Local/Instance | `make_local_proxy()` | `IGOR_LOCAL_DB_URL` → `IGOR_HOME_DB_URL` | `instance,clan,infra,public` | No (shares Igor DB today; own DB is the target) | In use via Igor fallback |
| Agent | `make_agent_proxy()` | `{DEVICE_ID_UPPER}_AGENT_DB_URL` → `IGOR_HOME_DB_URL` | `clan,infra,public` | Yes (one DB per device) | In use for Igor via `IGOR_HOME_DB_URL` |
| Client | `make_client_proxy(client_id)` | `{CLIENT_ID_UPPER}_CLIENT_DB_URL` | `client,public` | Yes — **mandatory** | Not yet provisioned |
| Infra (cross-agent) | `make_infra_proxy()` | `IGOR_HOME_DB_URL` | `infra,public` | No (sits on the Local DB long-term) | In use |

`{DEVICE_ID_UPPER}` is `os.getenv("DEVICE_ID", "igor").upper().replace("-","_").replace(".","_")`.
`{CLIENT_ID_UPPER}` is `client_id.upper().replace("-","_").replace(".","_")`.

---

### Tier 1: Global KB

**Purpose:** Universal compiled-inference patterns shared across all UU deployments. Bootstrap seed — a new instance clones this and has working patterns immediately.

**Write governance:** Writes go through a git contribution flow, not direct SQL. The Archivist proposes patterns; human review merges to main; the next `uu bootstrap` or explicit pull materializes into the local Postgres instance.

**Git/Postgres reconciliation:** The global tier lives in two forms:
- *Distribution form*: a forkable git repo of YAML/JSON pattern files (write governance, community contribution, domain forks).
- *Runtime form*: a local Postgres DB (`UU_GLOBAL_KB_DB_URL`) materialized from the git repo at bootstrap. Agents read from Postgres at runtime — not from git files directly.

Domain forks carry their own patterns and contribute generic ones upstream via PR. The Archivist device owns the local→git proposal flow.

**Schema (target):**

```sql
CREATE SCHEMA IF NOT EXISTS global;

CREATE TABLE IF NOT EXISTS global.compiled_rules (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_text   TEXT NOT NULL,
    category    TEXT,
    source_repo TEXT,              -- git repo + commit that produced this rule
    confidence  FLOAT DEFAULT 1.0,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS global.bootstrap_patterns (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_key TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    payload     JSONB,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

**Access:** All agents read. No agent writes directly — writes go through git PR flow only.

---

### Tier 2: Local/Instance DB

**Purpose:** This deployment's shared working memory. Ephemeral-to-medium-term state: ring memory, topic weights, pending replies, focus quality logs.

**Access:** All non-blocked local agents can read. Each agent writes to its own domain within the instance schema.

**Schema (current, on `instance`):**

| Table | Owner | Purpose |
|-------|-------|---------|
| `instance.ring_memory` | Igor | Short-term working memory ring buffer |
| `instance.twm_observations` | Igor | Topic weight matrix observations |
| `instance.pending_replies` | Igor | Queued channel reply backlog |
| `instance.tails` | Igor | Activation tails for memory prioritization |
| `instance.focus_quality_log` | Igor | Focus quality audit trail |
| `instance.proposals` | Igor | Proposed actions awaiting review |

**Target:** Separate Postgres instance from the Agent DB (`IGOR_LOCAL_DB_URL`). Currently shares `igor-wild-0001` via `IGOR_HOME_DB_URL` fallback.

---

### Tier 3: Agent DB (per device)

**Purpose:** Each device's private long-term memory. Igor's semantic memory lives here; future devices (Granny's build history, Vetinari's task state) will have their own isolated instances.

**Access:** Owner device reads/writes freely. Other agents may NOT connect directly — requests go via bus (see access control model below).

**Schema (current for Igor, on `clan`):**

| Table | Purpose |
|-------|---------|
| `clan.memories` | Semantic memory nodes |
| `clan.interpretive_edges` | Edge weights between memories |
| `clan.wg_cooccur` | Word graph co-occurrence matrix |
| `clan.reading_list` | Items queued for ingestion |

**Future schema name:** `agent` (for non-Igor devices). Igor continues using `clan` for backward compat; the `IGOR_HOME_SEARCH_PATH` env var controls this.

**Env var convention:**
- Igor: `IGOR_AGENT_DB_URL` (or `IGOR_HOME_DB_URL` as legacy fallback)
- Granny: `GRANNY_WEATHERWAX_AGENT_DB_URL`
- Each device only has its OWN URL in its environment — isolation by deployment, not by code gate.

---

### Tier 4: Client DB (per human)

**Purpose:** Per-human private data — preferences, access grants, personal conversation history. Contains no data from other clients.

**Isolation requirement:** Must be a **separate Postgres instance** per client (not just a schema). Cross-client data is structurally impossible at the DB boundary.

**Access:** Client-private by default. Owner controls which agents receive access grants. Agent-to-client access requires an explicit grant stored in `client.access_grants`.

**Schema (target):**

```sql
CREATE SCHEMA IF NOT EXISTS client;

CREATE TABLE IF NOT EXISTS client.personal_preferences (
    key         TEXT PRIMARY KEY,
    value       JSONB,
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS client.access_grants (
    device_id   TEXT NOT NULL,
    scope       TEXT NOT NULL,     -- e.g. 'read:preferences', 'read:history'
    granted_at  TIMESTAMPTZ DEFAULT now(),
    expires_at  TIMESTAMPTZ,
    PRIMARY KEY (device_id, scope)
);

CREATE TABLE IF NOT EXISTS client.conversation_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

**Env var convention:** `AKIEN_CLIENT_DB_URL`, `LEAH_CLIENT_DB_URL`, etc.

---

## Access control model

A device accesses data in this precedence:
1. **Own tier** — Agent DB (own), Local DB, Global DB (read) → direct `make_*_proxy()` call.
2. **Another agent's Agent DB** → bus request only. The requesting device sends a `db_query_request` envelope to `comms://{target_device}`; the target device executes the query under its own credentials and replies to `from_device`.
3. **A client DB** → only if the client's `access_grants` table contains a row for `(device_id, scope)`. The client device mediates — no agent connects to a client DB without an explicit grant.

### Bus inter-agent query protocol (message shape)

When a device needs data from another device's Agent DB, it sends to `comms://{target_device}`:

```json
{
  "type": "db_query_request",
  "query": "SELECT id, narrative FROM memories WHERE memory_type = %s LIMIT 10",
  "params": ["compiled_rule"],
  "reason": "escalation routing context",
  "reply_to": "comms://granny-weatherwax"
}
```

The target device responds to `reply_to` with:
```json
{
  "type": "db_query_response",
  "rows": [...],
  "error": null
}
```

The target device applies its own authorization policy (e.g., only return rows where `portable=true`, or reject unknown callers). **No device implements raw SQL pass-through.** The bus request is not a SQL proxy — it is a typed data access contract.

Implementation of the request handler (the receiving device's shim) is a follow-on ticket.

---

## Migration path from Igor-wild-0001 single DB

Currently all tiers co-habit `igor-wild-0001` under separate schemas: `clan` (Agent), `instance` (Local), `infra` (cross-agent), `public` (datacenter). The migration proceeds in stages:

### Stage 0 — Connection config (this ticket)
- Factory functions for all tiers are wired up in `db_proxy.py`.
- Env vars defined for each tier (see connection map above).
- No data moves; all tiers still resolve to `IGOR_HOME_DB_URL` via fallback.

### Stage 1 — Local/Instance separation
- Provision a new Postgres instance for the Local tier.
- Set `IGOR_LOCAL_DB_URL` to the new instance.
- Migrate `instance.*` tables. Remove from `igor-wild-0001`.
- `make_local_proxy()` stops falling back to `IGOR_HOME_DB_URL`.

### Stage 2 — Global KB bootstrap
- Create the global KB git repo (T-global-kb-git-repo).
- Provision `UU_GLOBAL_KB_DB_URL` instance.
- Seed `global.compiled_rules` from the Archivist's compiled rules (T-archivist-global-contrib).
- `make_global_proxy()` goes live.

### Stage 3 — Agent DB per device
- Each new device gets its own `{DEVICE_ID}_AGENT_DB_URL` at provisioning time.
- Igor continues using `IGOR_HOME_DB_URL` (no migration of clan.* needed short-term).
- New devices (Granny, Vetinari, etc.) provision their own Agent DBs and never touch Igor's.

### Stage 4 — Client DB provisioning
- Provision one Postgres instance per human client.
- Set `AKIEN_CLIENT_DB_URL`, etc.
- `make_client_proxy("akien")` goes live.
- Transfer any personal data currently in `igor-wild-0001` (none today).

### Completion criteria
Migration is complete when:
- `igor-wild-0001` contains only `clan.*` (Igor Agent DB) + `infra.*` (cross-agent).
- All other tiers point to separate instances.
- `IGOR_LOCAL_DB_URL` ≠ `IGOR_HOME_DB_URL`.
- `UU_GLOBAL_KB_DB_URL` is set and seeded.
- At least one client DB is provisioned.

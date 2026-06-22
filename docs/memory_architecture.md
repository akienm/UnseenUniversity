# Igor Runtime Memory Architecture

**Scope:** This documents **Igor's runtime cognition memory** — the live memory
graph the agent thinks with. It is **not** the dev-process memory store (tickets,
decisions, slates) under `devlab/runtime/memory/`; that's a separate, unrelated
flat-file system.

**Last verified against code:** 2026-06-22 (`devices/igor/memory/cortex.py`,
`tree_index.py`, `models.py`). Where this doc says "designed" or "latent," it
means the code does **not** currently do it — see the status legend.

Status legend used throughout:

| Mark | Meaning |
|------|---------|
| ✅ | Implemented and running |
| 🔶 | Code exists but is **gated off** or **latent** (never fires in practice today) |
| 📐 | **Designed** in a decision doc, **not built** |

---

## 0. "Is this the thing I had in my head?" — read this first

If you came looking for the design described as *"a single append-only table of
nodes, plus smaller tables each its own tree indexing the nodes, that calve when
they grow past ~5K by dominant attractors"* — **that is a real, written design,
and you are remembering it almost verbatim.** It lives in two decisions:

- **`D-wg-node-tree-architecture-2026-06-08`** — the universal pattern: one large
  append-only node table with stable IDs, *small per-tree index tables* (≤ a
  threshold of rows), a shared node registry, and calving at a 5000-node
  threshold by dominant attractors. This is the doc your mental model came from.
- **`D-facia-index-tree-2026-06-17`** — the tree-of-trees refinement: a meta-tree
  whose nodes are the *facia* (entry points) of all other trees; calving at 5K.
- **`D-memory-flat-table-reform-2026-06-04`** — the earlier, foundational split:
  move content out of the tree into a flat `clan.memory_store` + a pure-index
  `clan.memory_tree`.

**Build status of that vision (the short version):**

| Piece of the vision | Status |
|---|---|
| Single node table with stable IDs | ✅ — it's `clan.memories` (today content **and** hierarchy live in the *same* table, not yet split) |
| A catalog of named trees over the shared node pool | ✅ — the `trees` table (a *query catalog*, see §2) |
| **Small per-tree index tables** (`memory_store` + `memory_tree` split) | 📐 designed, **not built** |
| Calving when a tree exceeds a threshold | 🔶 implemented but **gated off** by default |
| Calving **by dominant attractors** | 📐 designed; today's code splits by **deepest child**, not by attractor |
| The 5000-node WORD_GRAPH threshold | 🔶 present in code but **latent** (WORD_GRAPH nodes are stored flat, so the trigger never fires for them) |
| Attractors as emergent hot nodes | ✅ — `get_attractors()`, scored, see §4 |

So: the *concepts* are real and mostly present in some form, but the specific
"per-tree index tables that calve at 5K by attractors" machine is **design intent,
not running code**. The rest of this doc separates the two precisely.

---

## 1. The word "tree" means three different things — keep them apart

This is the single biggest source of confusion. Three distinct structures are all
called "tree":

1. ✅ **The `parent_id` forest *inside* the node table.** Every node in
   `clan.memories` has a `parent_id`/`children_ids`. This hierarchy is what
   calving actually operates on (`_find_tree_root`, `tree_size`, `_deepest_child`
   all walk `parent_id`). When code "calves a tree," it detaches a chunk of *this*
   forest. There is one forest, living in columns of the single node table.

2. ✅ **The `trees` catalog table.** A separate table where **each row is a named
   traversal pattern** — `(tree_id, name, facia_id, traversal_rules)`. A "tree"
   here is *a named entry point + rules for walking outward from it*. Trees in
   this sense **do not own nodes**: the same node can be the facia (entry) of one
   tree and an interior node of another (`tree_index.py:4-5`). It is **one table,
   one row per tree** — *not* a physical table per tree.

3. 📐 **The designed per-tree index tables.** `D-memory-flat-table-reform` and
   `D-wg-node-tree` propose splitting into a flat content store (`memory_store`)
   plus *many small index tables* (`memory_tree`, "small calving tree tables"),
   one per tree, each ≤ threshold rows. **This is the "smaller tables, each its
   own tree" you remember — and it is not built.** Today its job is split between
   structure #1 (the `parent_id` forest) and structure #2 (the `trees` catalog).

When this doc says "tree," it marks which one it means.

---

## 2. Conceptual architecture (what's running today)

### 2.1 The node store — one table ✅

All memory is rows in a **single Postgres table, `clan.memories`** (originally
`public.memories`; moved to the `clan` schema by migration `m050`). A *node* is
one row. Key properties:

- **Stable text ID** (`id TEXT PRIMARY KEY`) — timestamp-derived, never reused, so
  every reference-by-ID resolves forever (calving never renames a node).
- **`narrative`** — the content.
- **`memory_type`** — `ROOT`, `CORE_PATTERN`, `IDENTITY`, `PROCEDURAL`,
  `WORD_GRAPH`, etc. (the full set is the `MemoryType` enum,
  `devices/igor/memory/models.py:16-33`). `WORD_GRAPH` is **a type within this
  table, not a separate table** — ~57K low-level word nodes for spreading
  activation, stored flat (`cortex.py:2182`).
- **Affect** — `valence`, `arousal`, `dominance` (a VAD profile per node).
- **`activation_count`** — an integer counter bumped on every `search()`/`recall()`
  hit. It's a *salience signal*, not a timestamp (`cortex.py:82`). This is the
  primary input to attractor scoring.
- **`scope`** — `class` | `instance` | `session` (portability layer;
  `D-memory-scope-layers-2026-06-01`).
- **`embedding`** — vector for similarity search and orphan adoption.

### 2.2 Two ways nodes relate: the forest and the graph

- **Hierarchy (tree #1):** `parent_id` + `children_ids` form a forest. Roots have
  `parent_id = NULL`. This is the structure calving reshapes.
- **Graph:** `interpretive_edges` (a separate table — directed, weighted,
  typed by `direction`/`layer`) plus the inline `links_weighted` JSON. This is
  the associative web laid *over* the forest; the same nodes participate in both.
- **Spreading activation:** the `tails` table records activation traces (node,
  weight, trail, position, time) so recent co-activation ("heat") can be replayed
  (`cortex.py:470-481`, `get_tail_heat`).

### 2.3 Trees as a query catalog (tree #2) ✅

The `trees` table is a **catalog of named ways to read the pool**. A row is:
a `name` (e.g. `cp1_uncertainty`), a `facia_id` (the entry node), and
`traversal_rules` (`{"method": "interpretive"|"bfs_all", "max_depth": 3, ...}`).
`TreeIndex.traverse(name, cortex)` walks outward from the facia per the rules and
returns the nodes. The six Core Pattern nodes (CP1–CP6) are the canonical
well-known tree roots (`tree_index.py:241-270`).

A tree is a *lens*, not a container. This is why a node can be facia of one tree
and interior of several others at once.

### 2.4 Attractors — emergent, not labeled ✅

Attractors are simply **the most-activated, most-linked nodes** — nothing marks a
node as an attractor; it's computed on demand. Score:

```
attractor_score(node) = activation_count × (1 + inbound_interpretive_edges)
```

PROCEDURAL nodes (habits) are excluded — they're skills, not knowledge
attractors. `get_attractors(limit)` returns the top-scoring nodes, cached
in-process for 60s because the underlying query is a full-table scan
(`cortex.py:6493-6532`).

### 2.5 Calving — why, and what actually happens 🔶

**Why (the design rationale):** as a tree grows, it stops being navigable. Calving
splits an over-large tree into smaller ones so traversal stays cheap and topical —
the cognitive analogue of an iceberg calving.

**What the code does today** (`_maybe_calve`, `cortex.py:6658-6701`):

- It is **gated off by default**: `IGOR_CALVING_ENABLED` must be `"true"`
  (defaults to `false`), so in normal operation **calving does not run at all**.
- When enabled, on each `store()` it finds the node's tree root (`_find_tree_root`)
  and compares `tree_size(root)` to a threshold:
  - default **1000** nodes (`IGOR_CALVING_THRESHOLD`),
  - **5000** for `WORD_GRAPH` — but this is **latent**: WORD_GRAPH nodes are
    stored flat (no `parent_id`), so `_maybe_calve` never fires for them
    (`cortex.py:6667-6669`).
- If over threshold, it splits at the **deepest child** of the tree (or that
  node's parent) — **not** by dominant attractor. (Calving *by attractor* is the
  📐 designed behavior in `D-wg-node-tree`.)
- It **never** calves a protected node — `ROOT`, `CP1`–`CP6`, `ID1`–`ID14`,
  `PROC1`–`PROC49` (`_CALVING_PROTECTED`) — and never calves from the `ROOT`
  tree (CP structure is sacred).

**The split itself** (`calve_subtree`, `cortex.py:6796-6861`) — this is the seam
where tree #1 touches tree #2:

1. `UPDATE memories SET parent_id = NULL WHERE id = <split node>` — the node
   becomes a new root; **all descendants keep their `parent_id` unchanged** and
   follow automatically. **No IDs change**, so every reference still resolves.
2. For **both** resulting roots (the new one and the old root), it calls
   `ensure_blob_facia(...)` (`blob_facia.py`) to refresh the `trees` catalog
   entry — i.e. the forest split is reflected back into the named-tree catalog.

---

## 3. Implementation reference

### 3.1 `clan.memories` — the node table ✅

`devices/igor/memory/cortex.py:339-368`

```sql
CREATE TABLE IF NOT EXISTS memories (
    id                  TEXT PRIMARY KEY,        -- stable, never reused
    narrative           TEXT,
    memory_type         TEXT,                    -- MemoryType enum value
    parent_id           TEXT,                    -- forest link (tree #1); NULL = root
    children_ids        TEXT DEFAULT '[]',       -- forward refs (JSON array)
    link_ids            TEXT DEFAULT '[]',
    valence             REAL DEFAULT 0.0,        -- affect (V)
    activation_count    INTEGER DEFAULT 0,       -- salience counter; attractor input
    friction_history    TEXT DEFAULT '[]',
    timestamp           TEXT,
    metadata            JSONB DEFAULT '{}'::jsonb,
    embedding           TEXT,                    -- similarity / adoption
    arousal             REAL DEFAULT 0.0,        -- affect (A)
    dominance           REAL DEFAULT 0.0,        -- affect (D)
    portable            INTEGER DEFAULT 1,
    links_weighted      TEXT DEFAULT '{}',       -- inline weighted graph edges
    last_accessed       TEXT,
    source              TEXT,
    certainty           REAL DEFAULT 1.0,
    context_of_encoding TEXT,
    updated_at          TEXT,
    scope               TEXT DEFAULT 'class'     -- class | instance | session
);
-- indexes (cortex.py:364-368)
idx_memories_metadata_gin (GIN on metadata)
idx_memories_memory_type
idx_memories_parent_id        -- fast child lookup (forest walks)
idx_memories_activation       -- activation_count DESC (hot-node detection)
idx_memories_ne_scan          -- activation_count DESC WHERE type NOT IN (ROOT, CORE_PATTERN)
```

### 3.2 `trees` — the named-traversal catalog (tree #2) ✅

Schema (authoritative docstring, `tree_index.py:8-15`; the `CREATE` is applied via
DB migration, then moved to the `clan` schema by `m050` — there is no `CREATE
TABLE trees` literal in the Python source):

```
trees:
  tree_id         TEXT PK         -- timestamp ID; also registered in node_registry
  name            TEXT UNIQUE     -- e.g. "cp1_uncertainty"
  facia_id        TEXT FK memories.id   -- entry node for this tree
  traversal_rules JSONB           -- {"method": "interpretive"|"bfs_all", "max_depth": 3, ...}
  description     TEXT
  machine_id      TEXT
  created_at      TIMESTAMPTZ
```

Public API (`tree_index.py:17-22`): `create / get / traverse / list_all /
trees_at_node`. `create` is idempotent by `name`. Default traversal rules:
`tree_index.py:43-49`.

### 3.3 Related tables

- **`interpretive_edges`** (`cortex.py:412-423`): `from_id`, `to_id`, `direction`,
  `weight`, `layer`, payload columns. The weighted associative graph; `direction
  = 'adoption'` links orphans to attractors.
- **`tails`** (`cortex.py:470-481`): `node_id`, `weight`, `recorded_at`,
  `trail_id`, `sequence_pos`. Spreading-activation traces.
- **`ring_memory`**, **`twm_observations`** — short-term/working memory
  (moved to the `instance` schema by `m050`).

### 3.4 Attractor scoring ✅

`get_attractors(limit)` — `cortex.py:6493-6532`:

```sql
SELECT m.id
FROM memories m
LEFT JOIN interpretive_edges ie ON ie.to_id = m.id
WHERE m.memory_type NOT IN ('PROCEDURAL')
GROUP BY m.id, m.activation_count
ORDER BY m.activation_count * (1 + COUNT(ie.id)) DESC
LIMIT %s
```

60s in-process TTL cache (`_ATTRACTOR_CACHE`, `cortex.py:516-522`). Orphan adoption
(`adopt_orphans`, `cortex.py:6562-6648`, gated by `IGOR_NODE_ADOPTION_ENABLED`)
links parentless nodes to their nearest attractor by embedding cosine similarity.

### 3.5 Calving ⟨gated off by default⟩ 🔶

| Function | Location | Role |
|---|---|---|
| `_maybe_calve(memory)` | `cortex.py:6658-6701` | Gate + threshold check + pick split node (deepest child) |
| `calve_subtree(node_id)` | `cortex.py:6796-6861` | Detach (`parent_id=NULL`), refresh facia for both roots |
| `tree_size(root_id)` | `cortex.py:6731-6749` | Recursive `COUNT(*)` over the subtree |
| `_find_tree_root(node_id)` | `cortex.py:6751-6770` | Walk `parent_id` chain to root |
| `_deepest_child(root_id)` | `cortex.py:6772-6794` | Deepest node (capped at depth 30) = split candidate |

Gates / constants:
- `IGOR_CALVING_ENABLED` (default `false`) — master gate; off ⇒ calving never runs.
- `IGOR_CALVING_THRESHOLD` (default `1000`) — generic per-tree node cap.
- `_TYPE_THRESHOLDS = {WORD_GRAPH: 5000}` — higher cap for WORD_GRAPH (latent).
- `_CALVING_PROTECTED` = `ROOT` ∪ `CP1..CP6` ∪ `ID1..ID14` ∪ `PROC1..PROC49`.

---

## 4. Implemented vs. Designed vs. Latent — the gap map

| Capability | Status | Where |
|---|---|---|
| Single stable-ID node table | ✅ | `clan.memories` (`cortex.py:339`) |
| `parent_id` forest (tree #1) | ✅ | columns on `memories` |
| Named-traversal catalog (tree #2) | ✅ | `trees` table, `tree_index.py` |
| Emergent attractors (activation × edges) | ✅ | `get_attractors`, `cortex.py:6493` |
| Spreading activation / tails | ✅ | `tails`, `cortex.py:470` |
| Orphan→attractor adoption | 🔶 gated (`IGOR_NODE_ADOPTION_ENABLED`) | `cortex.py:6562` |
| Calving over a threshold | 🔶 gated (`IGOR_CALVING_ENABLED=false`) | `cortex.py:6658` |
| Calving **by deepest child** | ✅ (when enabled) | `cortex.py:6682` |
| Calving **by dominant attractor** | 📐 designed, not built | `D-wg-node-tree-2026-06-08` |
| 5000-node WORD_GRAPH calving | 🔶 latent (WG nodes flat) | `cortex.py:6667` |
| **Per-tree index tables** (content/index split: `memory_store` + `memory_tree`) | 📐 designed, not built (tree #3) | `D-memory-flat-table-reform-2026-06-04` |
| Facia-index tree-of-trees + tree-manager object | 📐 designed, not built | `D-facia-index-tree-2026-06-17` |

**Governing decisions:**
- `D-wg-node-tree-architecture-2026-06-08` — the universal node+tree+calving vision.
- `D-facia-index-tree-2026-06-17` — tree-of-trees / facia index, calving at 5K.
- `D-memory-flat-table-reform-2026-06-04` — flat store + tree-as-index split.
- `D-memory-scope-layers-2026-06-01` — class/instance/session scope.
- `D-clan-template-memory-ownership-2026-06-01` — which device owns which tables.

(Decision docs live in `devlab/design_docs/decisions/` and, projected, in
`devlab/runtime/memory/decisions/`.)

---

## 5. Maintenance map (where to look when you change things)

| You're touching… | Read first |
|---|---|
| Node columns / schema migrations | `cortex.py:339-368` + `_SCHEMA_MIGRATIONS` (`cortex.py:542+`) |
| What a memory_type means | `models.py:16-33` (`MemoryType` enum) |
| Named trees / traversal | `tree_index.py` (whole file is small) |
| Attractor ranking | `get_attractors`, `cortex.py:6493` |
| Calving behavior or gates | `_maybe_calve` + `calve_subtree`, `cortex.py:6658-6861` |
| Spreading activation | `tails` schema + `get_tail_heat`, `cortex.py:470`, `3717` |
| The cortex's own design notes | `cortex.py:1-156` (module docstring — excellent inline reference) |

> If this doc and the code disagree, **the code wins** — update this doc. The
> status marks (✅/🔶/📐) are the most likely thing to drift as the designed
> pieces get built; re-check the gate flags and the `D-*` build status when you do.

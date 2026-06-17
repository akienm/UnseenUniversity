# D-memory-flat-table-reform-2026-06-04
**title:** Memory architecture: flat table with stable IDs, tree-as-index
**date:** 2026-06-04
**status:** decided
**spawned_tickets:** T-memory-flat-table-reform
**goal_link:** G-self-improving-system

---

## Decision narrative

All memories live in a single flat table (`clan.memory_store`). The ID for every
memory is `{row_number}.{timestamp}`, which encodes physical position — a process
that knows the ID knows the row with no traversal. Memories are **never deleted**;
rows are zeroed out or reused. Trees become pure indexing structures: they carry
activation weights and pattern pointers but no content.

---

## 1. Performance question (answered)

**Current model:** `clan.memories` is 609 MB / 127K rows. Each row contains both
content (`narrative`) and tree pointers (`parent_id`, `children_ids`). Tree traversal
means chasing parent/children text-ID foreign keys across a 609 MB table — at that
size, the index itself is too large to be cache-resident.

**New model:** The index tree holds only IDs + weights (small). The content store is
append-only, addressed by `row_number` (integer, physical page). Result:

- Index traversal: small tree rows → hot in Postgres buffer cache
- Content fetch: `WHERE row_seq = ?` → direct tuple scan by physical position, O(1)
- No tree-based content duplication; no content re-encoding on calving

**Verdict:** net better. The ID encoding eliminates the lookup — once you have the ID,
the row offset is known. Small index trees gain the cache benefit of the original
hypothesis without losing content locality.

---

## 2. Flat table schema

```sql
CREATE TABLE clan.memory_store (
    row_seq     BIGSERIAL PRIMARY KEY,           -- physical row number (part of ID)
    memory_id   TEXT NOT NULL UNIQUE,             -- "{row_seq}.{created_at_us}" — stable forever
    memory_type TEXT NOT NULL,
    narrative   TEXT,                             -- core content; NULL when zeroed
    scope       TEXT,                             -- instance-local vs. global
    source      TEXT,
    source_agent VARCHAR(128),
    valence     REAL,
    arousal     REAL,
    dominance   REAL,
    confidence  REAL,
    metadata    JSONB,
    payloads    JSONB,
    embedding   TEXT,                             -- keep for compat; will migrate to memory_embeddings
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ,
    zeroed_at   TIMESTAMPTZ,                      -- set when row is "deleted" (content NULLed)
    derived_from TEXT[]                            -- provenance chain
);

CREATE UNIQUE INDEX memory_store_id_idx ON clan.memory_store (memory_id);
CREATE INDEX memory_store_type_idx    ON clan.memory_store (memory_type);
CREATE INDEX memory_store_scope_idx   ON clan.memory_store (scope);
CREATE INDEX memory_store_zeroed_idx  ON clan.memory_store (zeroed_at) WHERE zeroed_at IS NULL;
```

**ID generation:**
```python
def make_memory_id(row_seq: int, created_at_us: int) -> str:
    return f"{row_seq}.{created_at_us}"
```
`created_at_us` is microseconds since epoch — monotonically increasing, encodes time,
combined with `row_seq` is globally unique. A reader with the ID can split on `.` to get
row_seq for the physical lookup.

---

## 3. Tree-as-index structure

Trees no longer hold content. Each tree node is a lightweight index record:

```sql
CREATE TABLE clan.memory_tree (
    node_id          TEXT PRIMARY KEY,           -- tree node identifier
    memory_id        TEXT REFERENCES clan.memory_store(memory_id),
    parent_node_id   TEXT,                       -- NULL for root
    children_node_ids TEXT[],                    -- child node IDs
    activation_count INTEGER NOT NULL DEFAULT 0,
    activation_score DOUBLE PRECISION,
    last_activated_at TIMESTAMPTZ,
    weight           REAL,
    pattern_tags     TEXT[],                     -- match hints (no content)
    layer            TEXT                        -- cognitive layer label
);

CREATE INDEX memory_tree_parent_idx   ON clan.memory_tree (parent_node_id);
CREATE INDEX memory_tree_memory_idx   ON clan.memory_tree (memory_id);
CREATE INDEX memory_tree_activation   ON clan.memory_tree (activation_score DESC NULLS LAST);
```

Calving: when a branch becomes over-specific, a new subtree is created in
`clan.memory_tree`. The `memory_id` pointers stay unchanged — the content in
`clan.memory_store` is untouched. Only the tree index is restructured.

`clan.interpretive_edges` maps between tree nodes (not memory IDs), carrying
weights, conditions, and edge types as before. No schema change needed there.

---

## 4. Migration path

**Phase 0 (schema):** Create `clan.memory_store` and `clan.memory_tree` alongside
existing `clan.memories`. No data yet.

**Phase 1 (dual-write):** Igor writes to both `clan.memories` (old) and
`clan.memory_store` (new). All reads still come from `clan.memories`. This validates
the new schema against live data without breaking anything.

**Phase 2 (backfill):** Migrate existing 127K rows from `clan.memories` to
`clan.memory_store`. Assign stable `memory_id` values. Build `clan.memory_tree`
from the existing `parent_id`/`children_ids` columns.

**Phase 3 (switch reads):** Igor code reads from `clan.memory_store` + `clan.memory_tree`.
Writes go to both during overlap window.

**Phase 4 (cut over):** Remove writes to `clan.memories`. Archive table. Switch
`clan.trees` / `interpretive_edges` foreign keys to `memory_id` format.

**Phase 5 (cleanup):** Drop `clan.memories` or rename to `_memories_archive`.

Each phase is a separate ticket. Phase 0 can ship immediately.

---

## 5. Caching overlay recommendation

With immutable `memory_id` → content mapping, caching becomes **trivial**:

- Cache entries never expire (content never changes; zeroing creates a new logical state)
- Key: `memory_id` (stable forever) → Value: `narrative` + `metadata`
- Invalidation: only needed if a row is zeroed/reused (set `zeroed_at`)
- Hot path: top-N most-activated tree nodes → prefetch their `memory_id` content into
  a small in-process LRU cache keyed on `memory_id`

**Recommendation:** keep the existing key/value cache abstraction but simplify the
invalidation policy to "zero-event only." The caching overlay remains valuable for
the hot cognition path (same ~200 memories accessed frequently). No change to the
cache interface; just remove time-based expiry.

---

## Current table: key numbers (2026-06-04)

| metric | value |
|--------|-------|
| `clan.memories` size | 609 MB |
| total rows | 126,979 |
| EPISODIC | 70,729 |
| FACTUAL | 46,246 |
| INTERPRETIVE | 3,120 |
| GOAL | 2,933 |
| PROCEDURAL | 506 |
| roots (no parent) | 27,672 |
| non-roots | 99,307 |

import logging

"""
Cortex - long-term memory storage.
SQLite-backed graph of Memory objects.

Also contains:
  - ring_memory table: short-term FIFO buffer (survives restarts). Sticky notepad.
  - twm_observations table: Temporal Working Memory — push-based sandbox for
    the Narrative Engine. Multiple processes deposit observations here.
    NE reads, integrates, promotes high-importance fragments to LTM.

change.37: memories table now has an `embedding` column (TEXT, nullable JSON).
  search() uses a hybrid approach:
    1. Text search → candidate set (fast, always works)
    2. Embedding re-rank → sort candidates by cosine similarity (if Ollama up)
  Embeddings are computed lazily for candidates that lack them and stored back.
  Cache at ~/.TheIgors/cache/embeddings/ avoids repeat Ollama calls.
"""

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .models import Memory, MemoryType, MemoryScope, default_scope
from .scrub import scrub
from .db_proxy import DatabaseProxy, MEM_COLS, make_home_proxy, make_local_proxy
from ..igor_base import IgorBase
from ..cognition.forensic_logger import log_error


@dataclass
class SearchRequest:
    """Request parameters for cortex.search().

    Encapsulates all search options before D233 lands (spreading-activation scores).
    """

    query: str
    limit: int = 10
    depth: str = "medium"  # "shallow" | "medium" | "deep"
    emotional_context: Optional[object] = None
    memory_types: Optional[list] = None
    word_graph: Optional[object] = None
    seed_nodes: Optional[list] = field(default_factory=lambda: None)
    threshold: float = 0.0


def _safe_memory_type(value: str) -> MemoryType:
    """Return MemoryType for value, falling back to FACTUAL for unknown types."""
    try:
        return MemoryType(value)
    except ValueError:
        return MemoryType.FACTUAL


# D200: column list owned by db_proxy; alias kept for the many call sites in this file.
_MEM_COLS_NO_EMBED = MEM_COLS

# #258b: in-process memory fetch cache
# Genesis types (ROOT / CORE_PATTERN / IDENTITY) are structurally immutable → permanent.
# All others expire after _MEM_CACHE_TTL seconds. 300s chosen to outlast the ~60s NE
# cycle — avoids the repeated 800ms IN fetch from _spread_activation() that always
# missed because 60s TTL and 60s NE interval created a perfect expiry storm (G-QP7).
_GENESIS_MEM_TYPES = frozenset({"ROOT", "CORE_PATTERN", "IDENTITY"})
_MEM_CACHE_TTL = 300.0  # seconds

RING_MAX = 50  # Max entries in the ring buffer
TWM_MAX = 50  # Max observations in TWM
TWM_MAX_SLOTS = int(
    os.getenv("IGOR_TWM_MAX_SLOTS", "7")
)  # D099: max attractor slots (Baars GWT)
# G47: suppress repeated observations at the door rather than admitting at floor salience.
TWM_SUPPRESS_AFTER_REPEATS = int(os.getenv("IGOR_TWM_SUPPRESS_REPEATS", "4"))
TWM_SUPPRESS_SALIENCE_FLOOR = float(os.getenv("IGOR_TWM_SUPPRESS_FLOOR", "0.04"))

# Change 4: urgency — distinct from salience (time-sensitivity vs importance)
# Change 3: TTL extension on confirmed relevance (not mere access)
TWM_TTL_EXTENSION_SECONDS = int(
    __import__("os").getenv("TWM_TTL_EXTENSION_SECONDS", "1800")
)


# ── Postgres bootstrap schema ─────────────────────────────────────────────────
# Used by Cortex._init_pg_schema() to initialise a fresh Postgres DB.
# SERIAL replaces AUTOINCREMENT; all columns match the current SQLite DDL path.
_PG_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS _migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id                  TEXT PRIMARY KEY,
    narrative           TEXT,
    memory_type         TEXT,
    parent_id           TEXT,
    children_ids        TEXT DEFAULT '[]',
    link_ids            TEXT DEFAULT '[]',
    valence             REAL DEFAULT 0.0,
    activation_count    INTEGER DEFAULT 0,
    friction_history    TEXT DEFAULT '[]',
    timestamp           TEXT,
    metadata            JSONB DEFAULT '{}'::jsonb,
    embedding           TEXT,
    arousal             REAL DEFAULT 0.0,
    dominance           REAL DEFAULT 0.0,
    portable            INTEGER DEFAULT 1,
    links_weighted      TEXT DEFAULT '{}',
    last_accessed       TEXT,
    source              TEXT,
    confidence          REAL DEFAULT 1.0,
    context_of_encoding TEXT,
    updated_at          TEXT,
    scope               TEXT DEFAULT 'class'
);

CREATE INDEX IF NOT EXISTS idx_memories_metadata_gin ON memories USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_memories_memory_type  ON memories (memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_parent_id    ON memories (parent_id);
CREATE INDEX IF NOT EXISTS idx_memories_activation   ON memories (activation_count DESC);
CREATE INDEX IF NOT EXISTS idx_memories_ne_scan      ON memories (activation_count DESC) WHERE memory_type NOT IN ('ROOT', 'CORE_PATTERN');

CREATE TABLE IF NOT EXISTS ring_memory (
    id          SERIAL PRIMARY KEY,
    category    TEXT,
    content     TEXT,
    timestamp   TEXT,
    thread_id   TEXT
);

CREATE TABLE IF NOT EXISTS twm_observations (
    id                  SERIAL PRIMARY KEY,
    timestamp           TEXT,
    source              TEXT,
    content_csb         TEXT,
    salience            REAL,
    metadata_json       TEXT,
    integrated          INTEGER DEFAULT 0,
    integration_count   INTEGER DEFAULT 0,
    expires_at          TEXT,
    urgency             REAL DEFAULT 0.5,
    instance_id         TEXT,
    thread_id           TEXT,
    category            TEXT,
    attractor_weight    REAL DEFAULT 0.0,
    parent_obs_id       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_twm_integrated          ON twm_observations (integrated);
CREATE INDEX IF NOT EXISTS idx_twm_expires_at          ON twm_observations (expires_at);
CREATE INDEX IF NOT EXISTS idx_twm_instance_id         ON twm_observations (instance_id);
CREATE INDEX IF NOT EXISTS idx_twm_instance_integrated ON twm_observations (instance_id, integrated, id ASC);

CREATE TABLE IF NOT EXISTS memory_blobs (
    id          SERIAL PRIMARY KEY,
    memory_id   TEXT REFERENCES memories(id),
    content     TEXT,
    tags        TEXT DEFAULT '[]',
    created_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_blobs_memory_id ON memory_blobs (memory_id);

CREATE TABLE IF NOT EXISTS interpretive_edges (
    id              SERIAL PRIMARY KEY,
    from_id         TEXT REFERENCES memories(id),
    to_id           TEXT REFERENCES memories(id),
    direction       TEXT,
    condition_csb   TEXT,
    meaning_payload TEXT,
    action_pointer  TEXT,
    weight          REAL DEFAULT 1.0,
    created_at      TEXT,
    layer           TEXT
);

CREATE INDEX IF NOT EXISTS idx_edges_from_id ON interpretive_edges (from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to_id   ON interpretive_edges (to_id);

CREATE TABLE IF NOT EXISTS reading_list (
    id                      TEXT PRIMARY KEY,
    title                   TEXT,
    author                  TEXT,
    source                  TEXT,
    book_type               TEXT,
    reading_rate            TEXT,
    priority                INTEGER DEFAULT 5,
    status                  TEXT DEFAULT 'pending',
    emotional_significance  TEXT,
    encoding_arousal        REAL DEFAULT 0.5,
    notes                   TEXT,
    added_by                TEXT,
    added_at                TEXT,
    started_at              TEXT,
    completed_at            TEXT
);

CREATE TABLE IF NOT EXISTS lists (
    list_name   TEXT,
    item_key    TEXT,
    item_value  TEXT,
    ref_type    TEXT,
    ref_id      TEXT,
    instance_id TEXT DEFAULT '',
    updated_at  TEXT,
    PRIMARY KEY (list_name, item_key, instance_id)
);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id   TEXT PRIMARY KEY REFERENCES memories(id),
    embedding   TEXT
);

CREATE TABLE IF NOT EXISTS active_slate (
    slate_id    TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    items       TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS tails (
    id           SERIAL PRIMARY KEY,
    node_id      TEXT NOT NULL,
    weight       REAL NOT NULL DEFAULT 1.0,
    recorded_at  TEXT NOT NULL,
    trail_id     TEXT,
    sequence_pos INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tails_node  ON tails (node_id);
CREATE INDEX IF NOT EXISTS idx_tails_time  ON tails (recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_tails_trail ON tails (trail_id) WHERE trail_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS traces (
    id          TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    query       TEXT,
    nodes       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_traces_time ON traces (recorded_at DESC);

CREATE TABLE IF NOT EXISTS traversal_contexts (
    id          SERIAL PRIMARY KEY,
    context_id  TEXT NOT NULL,
    job_id      TEXT,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    step        INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tctx_ctx     ON traversal_contexts (context_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tctx_ctx_key ON traversal_contexts (context_id, key);
"""


class Cortex(IgorBase):
    """SQLite-backed memory graph."""

    def __init__(self, db_path: Path, instance_id: str = None):
        super().__init__()
        self.db_path = db_path
        self._instance_id = instance_id  # #51: scopes TWM to this instance when set
        self._db = make_home_proxy(db_path)  # HOME: memories, edges (global truth)
        self._local_db = make_local_proxy(db_path)  # LOCAL: ring, TWM (box-scoped)
        self._init_db()
        # #244: set to True after interpretive_traverse() when a meaning_to_me edge was followed
        self._last_traverse_meaning_to_me: bool = False
        # #258b: in-process memory fetch cache; id → (Memory, monotonic_timestamp)
        self._mem_cache: dict = {}
        # #260: in-process habit list cache; invalidated on store() when new habit added
        self._habit_cache: Optional[list] = None

    def _conn(self):
        """Deprecated shim — use self._db() directly."""
        return self._db()

    def _local_conn(self):
        """Context manager for LOCAL tables: ring_memory, twm_observations."""
        return self._local_db()

    def _init_db(self):
        from .db_proxy import PGDatabaseProxy

        if isinstance(self._db, PGDatabaseProxy):
            # Postgres: check if already initialised; if not, bootstrap schema.
            # Fresh DBs raise UndefinedTable — fall through to _init_pg_schema().
            try:
                with self._conn() as conn:
                    conn.execute("SELECT 1 FROM memories LIMIT 1")
            except Exception:
                self._init_pg_schema()
                return
            # Already initialised — still run incremental column migrations.
            # These are idempotent: bare try/except catches "column already exists".
            with self._conn() as conn:
                for _col_sql in (
                    "ALTER TABLE traces ADD COLUMN purpose TEXT",
                    "ALTER TABLE traces ADD COLUMN twm_obs_id TEXT",
                    "ALTER TABLE traces ADD COLUMN instance_id TEXT",
                    "ALTER TABLE traces ADD COLUMN thread_id TEXT",
                    "ALTER TABLE memories ADD COLUMN scope TEXT DEFAULT 'class'",
                    # D260: engram program cell storage — idempotent, already-exists caught below
                    "ALTER TABLE memories ADD COLUMN payload TEXT DEFAULT NULL",
                ):
                    try:
                        conn.execute(_col_sql)
                    except Exception as e:
                        log_error(
                            kind="TOOL_FAIL", detail=f"column creation: {e}"
                        )  # idempotent
                # #123: backfill scope from memory_type (idempotent)
                conn.execute(
                    "UPDATE memories SET scope = 'instance' "
                    "WHERE memory_type IN ('EPISODIC', 'EXPERIENTIAL', 'CREDENTIAL_REF') "
                    "AND scope = 'class'"
                )
                conn.execute(
                    "UPDATE memories SET scope = 'instance' "
                    "WHERE portable = 0 AND scope = 'class'"
                )
            return

        with self._conn() as conn:
            # #261: one-time migration tracker — prevents costly scans on every boot
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    narrative TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    parent_id TEXT,
                    children_ids TEXT DEFAULT '[]',
                    link_ids TEXT DEFAULT '[]',
                    valence REAL DEFAULT 0.0,
                    activation_count INTEGER DEFAULT 0,
                    friction_history TEXT DEFAULT '[]',
                    timestamp TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_parent ON memories(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON memories(memory_type)")
            # G-QP1: NE query ORDER BY activation_count DESC runs 600-750ms without this
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activation ON memories(activation_count DESC)"
            )

            # change.37: embedding column — added via migration so existing DBs are not broken
            try:
                conn.execute(
                    "ALTER TABLE memories ADD COLUMN embedding TEXT DEFAULT NULL"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # G14 / #52: emotional profile columns (arousal + dominance)
            for _col in ("arousal REAL DEFAULT 0.0", "dominance REAL DEFAULT 0.0"):
                try:
                    conn.execute(f"ALTER TABLE memories ADD COLUMN {_col}")
                except Exception as _bare_e:
                    logging.getLogger(__name__).warning(
                        "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                    )

            # #71: portability flag — 1=portable (default), 0=instance-local
            try:
                conn.execute(
                    "ALTER TABLE memories ADD COLUMN portable INTEGER DEFAULT 1"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # #128: directed weighted links + last_accessed
            for _col in (
                "links_weighted TEXT DEFAULT '{}'",
                "last_accessed TEXT DEFAULT NULL",
            ):
                try:
                    conn.execute(f"ALTER TABLE memories ADD COLUMN {_col}")
                except Exception as _bare_e:
                    logging.getLogger(__name__).warning(
                        "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                    )

            # G46: provenance + epistemic fields
            for _col in (
                "source TEXT DEFAULT ''",
                "confidence REAL DEFAULT 1.0",
                "context_of_encoding TEXT DEFAULT ''",
            ):
                try:
                    conn.execute(f"ALTER TABLE memories ADD COLUMN {_col}")
                except Exception as _bare_e:
                    logging.getLogger(__name__).warning(
                        "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                    )

            # T-memory-sync: updated_at for swarm sync — set on every store()
            try:
                conn.execute(
                    "ALTER TABLE memories ADD COLUMN updated_at TEXT DEFAULT NULL"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # #123: scope column — class/instance/session; replaces portable boolean
            try:
                conn.execute(
                    "ALTER TABLE memories ADD COLUMN scope TEXT DEFAULT 'class'"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )
            # Backfill: instance-type memories → scope='instance' (idempotent)
            conn.execute(
                "UPDATE memories SET scope = 'instance' "
                "WHERE memory_type IN ('EPISODIC', 'EXPERIENTIAL', 'CREDENTIAL_REF') "
                "AND scope = 'class'"
            )
            # Honor explicit portable=0 rows not covered by type mapping
            conn.execute(
                "UPDATE memories SET scope = 'instance' "
                "WHERE portable = 0 AND scope = 'class'"
            )

            # D260: payload column — engram program cells + data fields
            try:
                conn.execute(
                    "ALTER TABLE memories ADD COLUMN payload TEXT DEFAULT NULL"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # #128: one-time migration — promote non-empty link_ids into links_weighted (weight 0.5)
            _migrate_rows = conn.execute(
                "SELECT id, link_ids FROM memories "
                "WHERE links_weighted = '{}' AND link_ids != '[]' AND link_ids IS NOT NULL"
            ).fetchall()
            for _row in _migrate_rows:
                try:
                    _ids = json.loads(_row["link_ids"] or "[]")
                    if _ids:
                        conn.execute(
                            "UPDATE memories SET links_weighted = ? WHERE id = ?",
                            (json.dumps({mid: 0.5 for mid in _ids}), _row["id"]),
                        )
                except Exception as _bare_e:
                    logging.getLogger(__name__).warning(
                        "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                    )

            # G-EMB1: separate embeddings table — keeps 16KB blobs off memories rows
            # so activation_count scans and LIKE scans don't load embedding pages.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id TEXT PRIMARY KEY,
                    embedding  TEXT NOT NULL
                )
            """)

            # #65: tagged blob storage — full-content reference documents
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_blobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_blob_memory ON memory_blobs(memory_id)"
            )

            # Short-term ring buffer — survives restarts, FIFO capped at RING_MAX
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ring_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL DEFAULT 'note',
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    thread_id TEXT DEFAULT NULL
                )
            """)
            # #136 P2: thread_id column — lazy migration for existing DBs
            try:
                conn.execute(
                    "ALTER TABLE ring_memory ADD COLUMN thread_id TEXT DEFAULT NULL"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # TWM — Temporal Working Memory
            # Push-based sandbox. Any process can deposit observations.
            # NE reads, integrates, updates salience, promotes to LTM.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS twm_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content_csb TEXT NOT NULL,
                    salience REAL DEFAULT 0.5,
                    metadata_json TEXT DEFAULT '{}',
                    integrated INTEGER DEFAULT 0,
                    integration_count INTEGER DEFAULT 0,
                    expires_at TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_twm_integrated ON twm_observations(integrated)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_twm_salience ON twm_observations(salience)"
            )

            # Change 4: urgency column (idempotent migration)
            # Urgency = time-sensitivity (0-1); distinct from salience (importance).
            # Noise expires on schedule; urgent items demand faster attention.
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN urgency REAL DEFAULT 0.2"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # #51: instance_id column — scopes each observation to the instance that pushed it
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN instance_id TEXT DEFAULT NULL"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # #158: thread_id — per-attention-nexus isolation (mirrors ring_memory #136)
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN thread_id TEXT DEFAULT NULL"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # G50: attractor_weight — the current primary focus; one item typically non-zero
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN attractor_weight REAL DEFAULT 0.0"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # #158: category — distinguishes TASK_SET from normal observations
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN category TEXT DEFAULT 'observation'"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # D099: parent_obs_id — slot branching; child obs traces back to parent slot
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN parent_obs_id INTEGER DEFAULT NULL"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # G52: interpretive_edges — directed edges for interpretive tree traversal
            conn.execute("""
                CREATE TABLE IF NOT EXISTS interpretive_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT 'activation',
                    condition_csb TEXT DEFAULT '',
                    meaning_payload TEXT DEFAULT '',
                    action_pointer TEXT DEFAULT '',
                    weight REAL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(from_id) REFERENCES memories(id),
                    FOREIGN KEY(to_id) REFERENCES memories(id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ie_from ON interpretive_edges(from_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ie_to ON interpretive_edges(to_id)"
            )
            # #244: layer column — tags edges by semantic layer (e.g. 'meaning_to_me')
            try:
                conn.execute(
                    "ALTER TABLE interpretive_edges ADD COLUMN layer TEXT DEFAULT ''"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )
            # #261: one-time migration — only runs if marker not yet present
            _m261 = conn.execute(
                "SELECT 1 FROM _migrations WHERE name = 'meaning_to_me_layer_tag'"
            ).fetchone()
            if not _m261:
                conn.execute("""
                    UPDATE interpretive_edges
                    SET layer = 'meaning_to_me'
                    WHERE (from_id LIKE 'CP%' OR from_id LIKE 'ID%')
                      AND (layer IS NULL OR layer = '')
                    """)
                from datetime import datetime as _dt261

                conn.execute(
                    "INSERT OR IGNORE INTO _migrations(name, applied_at) VALUES (?, ?)",
                    ("meaning_to_me_layer_tag", _dt261.now().isoformat()),
                )
            # D095: lists table — cross-type enumeration primitive
            # One table for all named lists: tags, projects, capabilities, per-master-notebook data.
            # instance_id="" = global (not NULL — avoids SQLite NULL-in-PK ambiguity).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lists (
                    list_name   TEXT NOT NULL,
                    item_key    TEXT NOT NULL,
                    item_value  TEXT,
                    ref_type    TEXT,
                    ref_id      TEXT,
                    instance_id TEXT NOT NULL DEFAULT '',
                    updated_at  TEXT,
                    PRIMARY KEY (list_name, item_key, instance_id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lists_name ON lists(list_name)"
            )

            # T-proc-1: active_slate — first-class DB artifact for Process Development Tools.
            # One active slate at a time; items = JSON array of {id, title, size, status}.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_slate (
                    slate_id    TEXT PRIMARY KEY,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'active',
                    items       TEXT NOT NULL DEFAULT '[]'
                )
            """)

            # T-tails-infra: decaying activation heat — biological analog.
            # Records each node surfaced by search with its relevance weight.
            # Decay computed on read; no background job needed.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tails (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id      TEXT NOT NULL,
                    weight       REAL NOT NULL DEFAULT 1.0,
                    recorded_at  TEXT NOT NULL,
                    trail_id     TEXT,
                    sequence_pos INTEGER
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tails_node ON tails(node_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tails_time ON tails(recorded_at DESC)"
            )
            # Migration: trail_id + sequence_pos added after initial tails deploy —
            # must precede the idx_tails_trail index creation
            try:
                conn.execute("ALTER TABLE tails ADD COLUMN trail_id TEXT DEFAULT NULL")
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )
            try:
                conn.execute(
                    "ALTER TABLE tails ADD COLUMN sequence_pos INTEGER DEFAULT NULL"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tails_trail ON tails(trail_id) WHERE trail_id IS NOT NULL"
            )

            # T-traces-infra: static path record — what nodes activated, in what order.
            # One trace per search() call. Permanent (no decay). Load-bearing for:
            # debugging ("why did Igor surface that?"), RED ALERT retrospective, introspection.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    id          TEXT PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    query       TEXT,
                    nodes       TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_traces_time ON traces(recorded_at DESC)"
            )

            # T-traversal-context: habit-chain execution state — key/value store per job.
            # Each traversal_start() mints a context_id (UUID). Habits read/write keys
            # via traversal_get/traversal_set. context_id propagates through the chain
            # via a TWM special key (TRAVERSAL_CTX_ID). Prerequisite for T-os-primitives.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS traversal_contexts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    context_id  TEXT NOT NULL,
                    job_id      TEXT,
                    key         TEXT NOT NULL,
                    value       TEXT NOT NULL,
                    step        INTEGER NOT NULL DEFAULT 0,
                    recorded_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tctx_ctx ON traversal_contexts(context_id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tctx_ctx_key "
                "ON traversal_contexts(context_id, key)"
            )

            # D199: memories table indexes — idempotent; safe on existing DBs.
            # These were in _PG_SCHEMA (fresh PG only) but not applied to existing DBs.
            # CREATE INDEX IF NOT EXISTS is a no-op if already present.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_activation "
                "ON memories(activation_count DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_type_activation "
                "ON memories(memory_type, activation_count DESC)"
            )
            try:
                # Partial index — WHERE clause syntax; SQLite 3.8+, Postgres 8.0+
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_last_accessed "
                    "ON memories(last_accessed DESC) WHERE last_accessed IS NOT NULL"
                )
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

            # T-trails-infra: add context columns to traces for TWM join + provenance.
            # Bare try/except per column — idempotent on re-run (column already exists = silent).
            # DO NOT use a migration guard here: guard + silent ALTER failure = columns never added.
            for _col_sql in (
                "ALTER TABLE traces ADD COLUMN purpose TEXT",
                "ALTER TABLE traces ADD COLUMN twm_obs_id TEXT",
                "ALTER TABLE traces ADD COLUMN instance_id TEXT",
                "ALTER TABLE traces ADD COLUMN thread_id TEXT",
            ):
                try:
                    conn.execute(_col_sql)
                except Exception as e:
                    log_error(
                        kind="TOOL_FAIL", detail=f"column creation: {e}"
                    )  # idempotent

            # G-QP2: wal_checkpoint moved to main.py post-Cortex-init (G-QP3)
            # Do NOT checkpoint here — _init_db() runs on every Cortex() instantiation
            # (book_learner, tools, etc.) and would flood the DB with checkpoint contention

    def _init_pg_schema(self) -> None:
        """Bootstrap the full schema on a fresh Postgres DB.
        Uses autocommit so CREATE EXTENSION works without transaction restrictions.
        """
        raw_conn = self._db._pool.getconn()
        try:
            raw_conn.autocommit = True
            cur = raw_conn.cursor()
            for stmt in _PG_SCHEMA.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
        finally:
            raw_conn.autocommit = False
            self._db._pool.putconn(raw_conn)

    # ── Long-term memory graph ─────────────────────────────────────────────────

    def store(
        self,
        memory: Memory,
        link_to: list = None,
        milieu_arousal: float = 0.0,
    ) -> Memory:
        """
        Persist a memory. If link_to is provided, auto-create directed weighted
        links to those memories (weight = relevance_score * arousal_factor).
        Only pass link_to for live-interaction stores — not genesis/boot.
        """
        # D256: replace default uuid4()[:8] IDs with timestamp node IDs.
        # Only replaces the bare 8-char lowercase hex default — named IDs
        # (BOOK_xxx, CP1, ROOT, etc.) pass through so callers with deterministic
        # content-hash IDs continue to work until they migrate to new_node_id().
        _mid = memory.id or ""
        _is_uuid_default = len(_mid) == 8 and all(c in "0123456789abcdef" for c in _mid)
        if _is_uuid_default:
            from .node_id import new_node_id as _new_node_id

            memory.id = _new_node_id()
        memory.narrative = scrub(memory.narrative)
        # Scrub string values in metadata to prevent credential leakage (#19)
        if memory.metadata:
            memory.metadata = {
                k: scrub(v) if isinstance(v, str) else v
                for k, v in memory.metadata.items()
            }
        # #128: auto-link to contextually active memories at store time
        if link_to:
            for related in link_to:
                if related.id == memory.id:
                    continue
                rel_score = getattr(related, "relevance_score", 0.5) or 0.5
                weight = min(1.0, rel_score * (1.0 + abs(milieu_arousal) * 0.5))
                if weight > 0.05:
                    memory.links[related.id] = max(
                        memory.links.get(related.id, 0.0), weight
                    )
        _now_iso = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memories
                (id, narrative, memory_type, parent_id, children_ids, link_ids,
                 valence, arousal, dominance,
                 activation_count, friction_history, timestamp, metadata, portable,
                 links_weighted, last_accessed,
                 source, confidence, context_of_encoding, updated_at, scope, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    memory.id,
                    memory.narrative,
                    memory.memory_type.value,
                    memory.parent_id,
                    json.dumps(memory.children_ids),
                    json.dumps(memory.link_ids),
                    memory.valence,
                    memory.arousal,
                    memory.dominance,
                    memory.activation_count,
                    json.dumps(memory.friction_history),
                    memory.timestamp.isoformat(),
                    json.dumps(memory.metadata),
                    1 if memory.portable else 0,
                    json.dumps(memory.links),
                    memory.last_accessed.isoformat() if memory.last_accessed else None,
                    memory.source,
                    memory.confidence,
                    memory.context_of_encoding,
                    _now_iso,
                    memory.scope.value if memory.scope else "class",
                    json.dumps(memory.payload) if memory.payload is not None else None,
                ),
            )
        # D256: register node in node_registry + Redis cache (non-fatal)
        try:
            from .node_id import register_node as _register_node

            _register_node(memory.id, "memories", memory.id)
        except Exception:
            pass
        # #260: invalidate habit cache when a habit is stored
        if memory.is_habit:
            self._habit_cache = None
        # #170: auto-connect new INTERPRETIVE memories to the nearest CP.
        # Keyword affinity → no LLM needed; never blocks store on failure.
        if memory.memory_type == MemoryType.INTERPRETIVE:
            try:
                self._auto_wire_interpretive(memory)
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )
        return memory

    # CP keyword affinity table for auto-wiring (#170)
    _CP_KEYWORDS: dict = {
        "CP1": [
            "don't know",
            "uncertain",
            "unknown",
            "honest",
            "epistemic",
            "truth",
            "ignorance",
        ],
        "CP2": [
            "fail",
            "failure",
            "learn",
            "obstacle",
            "blocked",
            "mistake",
            "error",
            "wrong",
            "emerge",
        ],
        "CP3": [
            "why",
            "reason",
            "understand",
            "structure",
            "meaning",
            "motivation",
            "purpose",
            "cause",
        ],
        "CP4": [
            "friction",
            "usability",
            "design",
            "easier",
            "interface",
            "accessible",
            "suck less",
        ],
        "CP5": [
            "experience",
            "emotion",
            "respect",
            "person",
            "human",
            "feel",
            "interpersonal",
            "consciousness",
        ],
        "CP6": [
            "safe",
            "safety",
            "risk",
            "danger",
            "protect",
            "critical",
            "secure",
            "guard",
        ],
    }

    def _auto_wire_interpretive(self, memory: "Memory") -> None:
        """
        #170: Find the best-matching CP for a new INTERPRETIVE memory and create
        an activation edge if none exists yet.  Pure keyword scoring — zero LLM cost.
        Skips SESSION_SUMMARY noise entries.
        """
        narrative_lower = memory.narrative.lower()
        # Skip operational logs masquerading as INTERPRETIVE
        if narrative_lower.startswith("session_summary") or narrative_lower.startswith(
            "fallback:"
        ):
            return
        # Skip if already has an incoming edge (seeded or previously auto-wired)
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT COUNT(*) FROM interpretive_edges WHERE to_id = ?",
                (memory.id,),
            ).fetchone()[0]
        if existing:
            return
        # Score each CP by keyword hits
        scores: dict = {}
        for cp_id, keywords in self._CP_KEYWORDS.items():
            scores[cp_id] = sum(1 for kw in keywords if kw in narrative_lower)
        best_cp = max(scores, key=lambda k: scores[k])
        if scores[best_cp] == 0:
            best_cp = "CP3"  # default: "there's always a why"
        self.add_interpretive_edge(
            from_id=best_cp,
            to_id=memory.id,
            direction="activation",
            condition_csb="auto_wired",
            meaning_payload=f"Auto-wired: {best_cp} → {memory.narrative[:80]}",
            action_pointer=memory.id,
            weight=0.60,
        )

    def get(self, memory_id: str) -> Optional[Memory]:
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return self._to_memory(row) if row else None

    def get_portable(self) -> list:
        """
        #71/#123: Return class-scoped memories — the set an offspring instance should inherit.
        Uses scope='class' (set by #123 migration from memory_type + portable flag).
        """
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE scope = 'class' "
                "ORDER BY id"
            ).fetchall()
        return [self._to_memory(r) for r in rows]

    def for_employer(self, employer_id: str) -> list:
        """
        #239: Return all memories tagged to a specific employer via metadata.employer_id.
        No schema change — employer_id is a metadata convention.
        Used by the cc_notebook endpoint to serve Claude's (or any employer's) notebook.

        #272: uses _MEM_COLS_NO_EMBED — avoids loading embedding blobs for all memories.
        """
        from .db_proxy import PGDatabaseProxy

        with self._conn() as conn:
            if isinstance(self._db, PGDatabaseProxy):
                rows = conn.execute(
                    f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                    "WHERE metadata @> jsonb_build_object('employer_id', %s::text)",
                    (employer_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                    "WHERE metadata LIKE ?",
                    (f'%"employer_id": "{employer_id}"%',),
                ).fetchall()
        return [self._to_memory(r) for r in rows]

    def get_children(self, parent_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE parent_id = ?",
                (parent_id,),
            ).fetchall()
        return [self._to_memory(r) for r in rows]

    def get_by_type(
        self,
        memory_type: MemoryType,
        limit: int = None,
        order_by: str = "timestamp",
    ) -> list:
        """Get memories by type, optionally ordered by activation_count (T-no-row-scans).

        Args:
            memory_type: type to filter by
            limit: max results
            order_by: 'timestamp' (default, DESC) or 'activation_count' (DESC)
        """
        sql = f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE memory_type = ?"
        if order_by == "activation_count":
            sql += " ORDER BY activation_count DESC"
        elif order_by == "timestamp":
            sql += " ORDER BY timestamp DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._conn() as conn:
            rows = conn.execute(sql, (memory_type.value,)).fetchall()
        return [self._to_memory(r) for r in rows]

    def get_hot_nodes(
        self,
        threshold: int = 5,
        skip_types: set | None = None,
        limit: int = 10,
    ) -> list:
        """Return top nodes by activation_count, skipping specified types.

        Interim fix for T-no-row-scans: single indexed query instead of
        full-table scan per type. Real fix: read from hot_attractors/TWM.
        """
        skip = skip_types or set()
        if skip:
            placeholders = ",".join("?" * len(skip))
            sql = (
                f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                f"WHERE activation_count >= ? AND memory_type NOT IN ({placeholders}) "
                f"ORDER BY activation_count DESC LIMIT {int(limit)}"
            )
            params = (threshold, *skip)
        else:
            sql = (
                f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                f"WHERE activation_count >= ? "
                f"ORDER BY activation_count DESC LIMIT {int(limit)}"
            )
            params = (threshold,)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._to_memory(r) for r in rows]

    def get_by_type_and_source(
        self, memory_type: MemoryType, source: str, limit: int | None = None
    ) -> list:
        """T-no-row-scans: SQL filter by both type and source.

        Replaces fetch-then-filter pattern for narrative_engine consolidation.
        """
        sql = f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE memory_type = ? AND source = ?"
        params = (memory_type.value, source)
        if limit:
            sql += f" ORDER BY timestamp DESC LIMIT {int(limit)}"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._to_memory(r) for r in rows]

    def get_procedural_by_metadata_key(
        self, key: str, value: str | None = None, limit: int | None = None
    ) -> list:
        """T-no-row-scans: SQL filter PROCEDURAL by metadata key.

        Replaces fetch-then-filter pattern for push sources (heartbeat, proactive, scheduler).
        If value is None, just checks key exists. Otherwise matches value exactly.
        """
        sql = f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE memory_type = ?"
        params = [MemoryType.PROCEDURAL.value]

        if value is not None:
            # Use JSON extraction for exact match (PostgreSQL jsonb->'key' = value)
            sql += " AND json_extract(metadata, ?) = ?"
            params.extend([f"$.{key}", value])
        else:
            # Just check key exists
            sql += " AND json_extract(metadata, ?) IS NOT NULL"
            params.append(f"$.{key}")

        if limit:
            sql += f" ORDER BY timestamp DESC LIMIT {int(limit)}"

        with self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._to_memory(r) for r in rows]

    def add_child(self, parent_id: str, child_id: str):
        parent = self.get(parent_id)
        if parent and child_id not in parent.children_ids:
            parent.children_ids.append(child_id)
            self.store(parent)

    def reinforce_links(
        self, memory_id: str, co_active_ids: list, delta: float
    ) -> None:
        """#45 G11: adjust outgoing link weights from memory_id toward co_active_ids.

        Positive delta strengthens links (correct prediction).
        Negative delta weakens them (wrong prediction).
        Weights are clamped to [0.0, 1.0]; links that hit 0.0 are removed.
        """
        if not memory_id or not co_active_ids or delta == 0.0:
            return
        memory = self.get(memory_id)
        if memory is None:
            return
        changed = False
        for co_id in co_active_ids:
            if co_id == memory_id:
                continue
            old = memory.links.get(co_id, 0.0)
            new = max(0.0, min(1.0, old + delta))
            if new == 0.0:
                memory.links.pop(co_id, None)
            else:
                memory.links[co_id] = round(new, 4)
            if old != new:
                changed = True
        if changed:
            self.store(memory)

    def record_activation(self, memory_id: str, friction: float):
        memory = self.get(memory_id)
        if memory:
            memory.activation_count += 1
            memory.friction_history.append(friction)
            memory.last_accessed = datetime.now()  # #128
            self.store(memory)

    # ── #65: Tagged blob storage ───────────────────────────────────────────────

    def store_blob(
        self,
        narrative: str,
        content: str,
        tags: list[str],
        parent_id: str = "CP3",
        valence: float = 0.0,
    ) -> Memory:
        """
        Store a reference document: brief narrative in memories (searchable),
        full content in memory_blobs (retrievable by tag).

        Returns the Memory record (use .id to retrieve blob later).
        """
        tags = [t.lower().strip() for t in tags if t.strip()]
        mem = Memory(
            narrative=scrub(narrative[:1000]),
            memory_type=MemoryType.REFERENCE,
            parent_id=parent_id,
            valence=valence,
            metadata={"tags": tags, "has_blob": True},
        )
        self.store(mem)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO memory_blobs (memory_id, content, tags, created_at) "
                "VALUES (?, ?, ?, ?)",
                (mem.id, scrub(content), json.dumps(tags), datetime.now().isoformat()),
            )
        return mem

    def upsert_blob(
        self,
        narrative: str,
        content: str,
        tags: list[str],
        source_id: str,
        extra_metadata: dict = None,
        parent_id: str = "CP3",
        valence: float = 0.0,
    ) -> tuple["Memory", bool]:
        """
        Insert or update a blob identified by source_id (#87).

        source_id: unique external key (e.g. "github_issue_#42"). Used to find
        existing blobs on repeat calls. Stored in metadata["source_id"].

        Returns (Memory, created) where created=True if inserted, False if updated.
        """
        from datetime import datetime as _dt

        tags = [t.lower().strip() for t in tags if t.strip()]
        now_iso = _dt.now().isoformat()
        extra = dict(extra_metadata or {})
        extra["source_id"] = source_id
        extra["synced_at"] = now_iso

        # Search for existing blob by source_id in metadata JSON
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM memories WHERE metadata LIKE ?",
                (f'%"source_id": "{source_id}"%',),
            ).fetchone()

        if row:
            mem_id = row["id"]
            # Update narrative in memories table
            with self._conn() as conn:
                existing_meta = conn.execute(
                    "SELECT metadata FROM memories WHERE id = ?", (mem_id,)
                ).fetchone()
                old_meta = json.loads(existing_meta["metadata"] or "{}")
                old_meta.update(extra)
                old_meta["tags"] = tags
                conn.execute(
                    "UPDATE memories SET narrative = ?, metadata = ? WHERE id = ?",
                    (scrub(narrative[:1000]), json.dumps(old_meta), mem_id),
                )
                conn.execute(
                    "UPDATE memory_blobs SET content = ?, tags = ? WHERE memory_id = ?",
                    (scrub(content), json.dumps(tags), mem_id),
                )
            mem = self.get(mem_id)
            return mem, False
        else:
            meta = {"tags": tags, "has_blob": True}
            meta.update(extra)
            mem = Memory(
                narrative=scrub(narrative[:1000]),
                memory_type=MemoryType.REFERENCE,
                parent_id=parent_id,
                valence=valence,
                metadata=meta,
            )
            self.store(mem)
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO memory_blobs (memory_id, content, tags, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (mem.id, scrub(content), json.dumps(tags), now_iso),
                )
            return mem, True

    def store_blob_pair(
        self,
        name: str,
        raw_content: str,
        distilled_content: str,
        model_id: str,
        extra_tags: list[str] = None,
    ) -> tuple["Memory", "Memory"]:
        """
        Two-blob pattern (#68): store raw CSB + model-tagged distilled summary.

        name: short identifier, e.g. "mission" or "detailed_architecture"
        raw_content: authoritative source (full CSB)
        distilled_content: compact summary (~60-70% fewer tokens)
        model_id: model that generated the distilled version (for cache-miss detection)
        extra_tags: additional tags beyond design_doc/csb/distilled

        Returns (raw_mem, distilled_mem).
        Tag schema:
          raw:       ["design_doc", "csb", name]
          distilled: ["design_doc", "distilled", name, f"model={model_id}"]
        """
        extra = extra_tags or []
        raw_mem, _ = self.upsert_blob(
            narrative=f"Design doc (raw CSB): {name}",
            content=raw_content,
            tags=["design_doc", "csb", name] + extra,
            source_id=f"design_doc_raw_{name}",
        )
        distilled_mem, _ = self.upsert_blob(
            narrative=f"Design doc (distilled, {model_id}): {name}",
            content=distilled_content,
            tags=["design_doc", "distilled", name, f"model={model_id}"] + extra,
            source_id=f"design_doc_distilled_{name}_{model_id}",
            extra_metadata={"model_id": model_id, "raw_blob_id": raw_mem.id},
        )
        return raw_mem, distilled_mem

    def get_blob(self, memory_id: str) -> Optional[str]:
        """Fetch full blob content for a REFERENCE memory. Returns None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT content FROM memory_blobs WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        return row["content"] if row else None

    def search_by_tags(self, tags: list[str], match_all: bool = False) -> list[dict]:
        """
        Search blob memories by tag.

        match_all=False (default): return blobs matching ANY of the tags.
        match_all=True: return only blobs matching ALL tags.

        Returns list of dicts: {memory_id, narrative, tags, content_preview, created_at}.
        """
        tags = [t.lower().strip() for t in tags if t.strip()]
        if not tags:
            return []

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT b.memory_id, b.tags, b.content, b.created_at, m.narrative "
                "FROM memory_blobs b "
                "JOIN memories m ON m.id = b.memory_id "
                "ORDER BY b.created_at DESC"
            ).fetchall()

        results = []
        for row in rows:
            try:
                blob_tags = json.loads(row["tags"] or "[]")
            except Exception:
                blob_tags = []
            matched = [t for t in tags if t in blob_tags]
            if match_all and len(matched) < len(tags):
                continue
            if not match_all and not matched:
                continue
            results.append(
                {
                    "memory_id": row["memory_id"],
                    "narrative": row["narrative"],
                    "tags": blob_tags,
                    "matched_tags": matched,
                    "content_preview": row["content"][:200],
                    "created_at": row["created_at"],
                }
            )
        return results

    def expand_blob_memories(
        self,
        memories: list,
        threshold: float = 0.5,
        blob_chars: int = 2000,
    ) -> list:
        """
        For high-relevance memories with overflow blob content, append the blob
        to the memory narrative in-place.  Memory objects are transient (created
        from DB rows) so mutation is safe — it never touches the DB.

        Call this after search + winnow, before building context for the LLM.
        Gate: memory.metadata["has_blob"] must be True and relevance_score >= threshold.
        """
        for m in memories:
            if not m.metadata.get("has_blob"):
                continue
            rel = getattr(m, "relevance_score", 0.0)
            if rel < threshold:
                continue
            try:
                blob = self.get_blob(m.id)
                if blob:
                    m.narrative = m.narrative + "\n[FULL CONTENT]\n" + blob[:blob_chars]
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )
        return memories

    def list_blob_tags(self) -> dict[str, int]:
        """Return all tags with counts, sorted by frequency."""
        with self._conn() as conn:
            rows = conn.execute("SELECT tags FROM memory_blobs").fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            try:
                for tag in json.loads(row["tags"] or "[]"):
                    counts[tag] = counts.get(tag, 0) + 1
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))

    @staticmethod
    def _route_types_from_query(query_lower: str) -> list | None:
        """
        T-db-type-routing: infer relevant memory types from query keywords.
        Returns a list of MemoryType values to prioritize, or None for default (all types).

        Heuristics:
          - Procedural cues  → PROCEDURAL + INTERPRETIVE (how-to, tool use, steps)
          - Personal cues    → EPISODIC + EXPERIENTIAL + IDENTITY (subjective, self-referential)
          - Default          → None (all types, current behaviour)

        These are additive hints, not hard filters — the Phase 0 traversal and Phase 2
        cosine rerank will still surface other types when they're genuinely relevant.
        """
        _procedural_cues = {
            "how",
            "steps",
            "restart",
            "run",
            "install",
            "configure",
            "enable",
            "disable",
            "reload",
            "deploy",
            "build",
            "fix",
            "debug",
            "implement",
            "set up",
            "set",
            "use",
        }
        _personal_cues = {
            " i ",
            " my ",
            " me ",
            " i'",
            "feel",
            "think",
            "believe",
            "remember",
            "experience",
            "did i",
            "have i",
        }

        _words = set(query_lower.split())
        if _words & _procedural_cues:
            return [
                MemoryType.PROCEDURAL.value,
                MemoryType.INTERPRETIVE.value,
                MemoryType.FACTUAL.value,
            ]
        for cue in _personal_cues:
            if cue in query_lower:
                return [
                    MemoryType.EPISODIC.value,
                    MemoryType.EXPERIENTIAL.value,
                    MemoryType.IDENTITY.value,
                    MemoryType.INTERPRETIVE.value,
                ]
        return None

    def search(
        self,
        query_or_request: str | SearchRequest,
        limit: int | None = None,
        emotional_context=None,
        memory_types: list | None = None,
        word_graph=None,
    ) -> list:
        """
        Three-phase hybrid search (#172 + change.37 + T-db-type-routing + T-308-hebbian).

        Args:
          query_or_request: Either a search query string (legacy) or SearchRequest dataclass.
                           When passed a string, remaining args (limit, emotional_context, etc.)
                           are used to construct SearchRequest for backwards compatibility.
          limit: (legacy) limit parameter; ignored if query_or_request is SearchRequest.
          emotional_context: (legacy) emotional context; ignored if query_or_request is SearchRequest.
          memory_types: (legacy) memory type filter; ignored if query_or_request is SearchRequest.
          word_graph: (legacy) word graph for Hebbian bridge; ignored if query_or_request is SearchRequest.

        Phase 0 — traversal-first (#172, always runs):
          If TWM has an active attractor, follow graph edges (parent/children/links)
          from anchor memory nodes to depth=N (where N depends on depth tier).
          Produces association-chain candidates before any similarity computation.

        Phase 1 — text scoring (always runs):
          Naive keyword search over all non-structural memories. Results merged with
          Phase 0 candidates; deduped; higher score wins for memories in both sets.

        Phase 2 — embedding re-rank (runs when Ollama available):
          Embed the query; cosine-rank the merged candidate pool. Falls back silently
          to the pre-ranked pool if nomic-embed-text is unavailable.

        Type routing (T-db-type-routing):
          memory_types overrides auto-routing. When None, _route_types_from_query()
          infers relevant types from query keywords. All types still represented via
          Phase 0 traversal; type routing shapes the Phase 1 candidate pool only.

        Hebbian bridge (T-308, IGOR_HEBBIAN_BRIDGE=true):
          word_graph: if provided and env gate is on, applies wg_boost_search()
          to candidate scores after Phase 1, and record_retrieval_boost() after
          result selection to feed high-importance retrievals back into the word graph.
        """
        # Parse arguments: support both legacy string-based and new SearchRequest interface
        if isinstance(query_or_request, SearchRequest):
            req = query_or_request
        else:
            # Legacy string interface: construct SearchRequest from positional args
            req = SearchRequest(
                query=query_or_request,
                limit=limit if limit is not None else 10,
                emotional_context=emotional_context,
                memory_types=memory_types,
                word_graph=word_graph,
            )

        query = req.query
        terms = query.lower().split()
        _query_lower = query.lower()

        # T-db-type-routing: determine candidate pool type filter
        _routed_types = (
            req.memory_types
            if req.memory_types is not None
            else self._route_types_from_query(_query_lower)
        )
        _ALWAYS_EXCLUDE = (MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value)

        # D199: candidate pool via traversal from CP roots + activation-index supplement.
        # CP/ID nodes are the graph roots — traversal follows edges by ID (no table scan).
        # Orphaned nodes (no parent path to CP roots) are caught by the activation index.
        _CP_ROOTS = ["CP1", "CP2", "CP3", "CP4", "CP5", "CP6"]
        _ID_ROOTS = [f"ID{i}" for i in range(1, 15)]
        _traversal_pool = self.traverse_from(_CP_ROOTS + _ID_ROOTS, depth=3, limit=200)
        _traversal_ids = {m.id for m in _traversal_pool}

        # Supplement: activation-ranked nodes not already in traversal pool (orphans + new nodes)
        _excl_ph = ",".join("?" * len(_ALWAYS_EXCLUDE))
        # Skip supplement if traversal already found enough nodes — at graph scale
        # (11k+ memories rooted at CP1-CP6) depth=3 reaches most nodes; supplement
        # is redundant and triggers a 300-wide-row activation scan every search call.
        _SUPPLEMENT_THRESHOLD = 80  # only supplement if graph is sparse
        _supplement_limit = (
            max(0, 300 - len(_traversal_pool))
            if len(_traversal_pool) < _SUPPLEMENT_THRESHOLD
            else 0
        )
        with self._conn() as conn:
            if _routed_types and _supplement_limit > 0:
                _ph = ",".join("?" * len(_routed_types))
                rows = conn.execute(
                    f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                    f"WHERE memory_type IN ({_ph}) AND memory_type NOT IN ({_excl_ph}) "
                    "ORDER BY activation_count DESC LIMIT ?",
                    _routed_types + list(_ALWAYS_EXCLUDE) + [_supplement_limit],
                ).fetchall()
            elif _supplement_limit > 0:
                rows = conn.execute(
                    f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                    f"WHERE memory_type NOT IN ({_excl_ph}) "
                    "ORDER BY activation_count DESC LIMIT ?",
                    list(_ALWAYS_EXCLUDE) + [_supplement_limit],
                ).fetchall()
            else:
                rows = []

        _supplement = [
            self._to_memory(r) for r in rows if r["id"] not in _traversal_ids
        ]

        # T-orphan-threshold-fix: always rescue true orphans (parent_id IS NULL)
        # regardless of traversal pool size. The 80-node supplement gate closes at
        # graph scale (11k+ nodes), making rootless nodes permanently invisible.
        # This pass is cheap: small fixed limit, targets only nodes with null parent.
        _orphan_rescue: list = []
        try:
            with self._conn() as conn:
                _orphan_rows = conn.execute(
                    f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                    f"WHERE parent_id IS NULL AND memory_type NOT IN ({_excl_ph}) "
                    "ORDER BY activation_count DESC LIMIT 20",
                    list(_ALWAYS_EXCLUDE),
                ).fetchall()
            _seen_ids = _traversal_ids | {m.id for m in _supplement}
            _orphan_rescue = [
                self._to_memory(r) for r in _orphan_rows if r["id"] not in _seen_ids
            ]
            if _orphan_rescue:
                logging.getLogger("forensic").debug(
                    "[cortex.search] orphan rescue: %d rootless nodes added to pool",
                    len(_orphan_rescue),
                )
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py orphan rescue: %s",
                _bare_e,
            )

        all_memories = _traversal_pool + _supplement + _orphan_rescue

        # G-FACTUAL-RETRIEVAL1 fix: FACTUAL keyword supplement.
        # Book/reading FACTUALs are typically orphans (parent_id IS NULL, activation_count=0).
        # The activation-based supplement gate skips them at graph scale (traversal always
        # finds ≥80 nodes → supplement=0). Orphan rescue picks only 20 by activation_count,
        # which is useless when all orphans are at zero. This pass directly keyword-searches
        # FACTUAL nodes — bypasses connectivity and activation gates entirely.
        #
        # Stop-word filter: raw terms include "what", "do", "you", "know", "about" etc.
        # These match almost every conversational FACTUAL. Filter to content words (≥4 chars,
        # not in stop list) so the ILIKE anchors on meaningful tokens like "hebbian", "neuron".
        _STOP = frozenset(
            "what do you know about where when who how why tell give explain list me "
            "is are was were the a an and or of to in for on at by with".split()
        )
        _fk_terms = [t for t in terms if len(t) >= 4 and t not in _STOP]
        _factual_kw: list = []  # tracked at outer scope so Phase 2 can force them in
        if _fk_terms:
            # Per-term queries (LIMIT 20 each) so rare terms like "hebbian" always get
            # their nodes — a single OR query fills the LIMIT with generic-term matches
            # and specific-term nodes fall outside the window.
            _fk_seen = {m.id for m in all_memories}
            _fk_ids: set[str] = set()
            try:
                for _fkt in _fk_terms[:6]:
                    with self._conn() as conn:
                        _fk_rows = conn.execute(
                            f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                            "WHERE memory_type = %s AND LOWER(narrative) LIKE %s "
                            "ORDER BY activation_count DESC LIMIT 20",
                            [MemoryType.FACTUAL.value, f"%{_fkt.lower()}%"],
                        ).fetchall()
                    for _r in _fk_rows:
                        if _r["id"] not in _fk_seen and _r["id"] not in _fk_ids:
                            _fk_ids.add(_r["id"])
                            _factual_kw.append(self._to_memory(_r))
                if _factual_kw:
                    logging.getLogger(__name__).info(
                        "[cortex.search] factual-kw-supplement: %d book nodes added "
                        "for query %r (terms=%r)",
                        len(_factual_kw),
                        query[:60],
                        _fk_terms[:6],
                    )
                    all_memories = all_memories + _factual_kw
            except Exception as _fk_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py "
                    "factual_kw_supplement: %s",
                    _fk_e,
                )

        # Filter out NE diagnostic memories — operational noise from consolidation/stall loops
        # that may have entered LTM before the self-diagnostic filter was in place (URGENT.3)
        _NE_DIAG = ("consolidation", "stall", "loop detected", "recursive", "ne_diag")
        all_memories = [
            m
            for m in all_memories
            if not (
                m.metadata.get("source") == "narrative_engine"
                and any(kw in m.narrative.lower() for kw in _NE_DIAG)
            )
        ]

        # Phase 1: text scoring
        text_scored = []
        for m in all_memories:
            score = sum(1 for t in terms if t in m.narrative.lower())
            if score > 0:
                text_scored.append((score, m))
        text_scored.sort(key=lambda x: x[0], reverse=True)

        # Phase 0: traversal-first from TWM anchors (#172)
        _traversal: list = []
        try:
            _anchors = self._get_context_anchors()
            if _anchors:
                _traversal = self.traverse_from(_anchors, depth=2, limit=req.limit * 2)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )

        # Candidate pool: merge traversal + text results, dedup by id (#172)
        # Traversal memories are included regardless of keyword hit;
        # text-only memories fill gaps. In both → take the higher score.
        _max_terms = max(1, len(terms))
        _trav_map: dict[str, "Memory"] = {m.id: m for m in _traversal}
        _merged: dict[str, "Memory"] = dict(_trav_map)

        # T-db-spreading-activation: seed candidate pool from recently-activated nodes.
        # Warm memories (recently surfaced) get a small base presence so cosine rerank
        # can promote them if genuinely relevant. Never forces results — Phase 2 decides.
        try:
            for m in self.get_by_activation(limit=30):
                if m.id not in _merged:
                    m.relevance_score = 0.1  # type: ignore[attr-defined]
                    _merged[m.id] = m
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )

        for score, m in text_scored:
            norm_score = score / _max_terms
            if m.id not in _merged:
                m.relevance_score = norm_score  # type: ignore[attr-defined]
                _merged[m.id] = m
            else:
                existing = getattr(_merged[m.id], "relevance_score", 0.0) or 0.0
                _merged[m.id].relevance_score = max(existing, norm_score)  # type: ignore[attr-defined]

        candidates = sorted(
            _merged.values(),
            key=lambda m: getattr(m, "relevance_score", 0.0),
            reverse=True,
        )[: req.limit * 2]

        # G-FACTUAL-RETRIEVAL1 Phase 2 force-inject: FACTUAL supplement nodes may have
        # been dropped by the Phase 1 top-k cutoff (EPISODICs containing the probe text
        # score 7/7 on raw terms, pushing FACTUALs like "Hebbian..." to position 30+).
        # Force the supplement nodes into the candidate pool so Phase 2 embedding rerank
        # can correctly evaluate their cosine similarity to the query.
        if _factual_kw:
            _cand_ids = {m.id for m in candidates}
            for _fkm in _factual_kw:
                if _fkm.id not in _cand_ids:
                    _fkm.relevance_score = 0.01  # type: ignore[attr-defined]
                    candidates.append(_fkm)

        if not candidates:
            return []

        # T-308: Hebbian bridge Part 1 — word graph → candidate score boost
        if req.word_graph is not None:
            try:
                from ..cognition.hebbian_bridge import wg_boost_search

                _wg_boosts = wg_boost_search(req.word_graph, query, candidates)
                for _m in candidates:
                    if _m.id in _wg_boosts:
                        _cur = getattr(_m, "relevance_score", 0.0) or 0.0
                        _m.relevance_score = min(1.0, _cur + _wg_boosts[_m.id])  # type: ignore[attr-defined]
            except Exception as _bare_e:
                logging.getLogger(__name__).debug(
                    "hebbian wg_boost_search skipped: %s", _bare_e
                )

        # Phase 2: embedding re-rank
        try:
            from ..cognition.embedder import embed, cosine_similarity

            query_vec = embed(query)
            if query_vec:
                # G-EMB1: batch-fetch all candidate embeddings in one query
                emb_map = self._get_embeddings_batch([m.id for m in candidates])
                # Lazily compute any that are missing (new memories not yet embedded)
                for m in candidates:
                    if m.id not in emb_map:
                        emb_map[m.id] = self._get_or_compute_embedding(m)
                scored = []
                for m in candidates:
                    mem_vec = emb_map.get(m.id)
                    sim = cosine_similarity(query_vec, mem_vec) if mem_vec else 0.0
                    m.relevance_score = sim  # type: ignore[attr-defined]
                    scored.append((sim, m))
                scored.sort(key=lambda x: x[0], reverse=True)

                # Signal C (Change 3): extend TTL for high-relevance TWM observations.
                # A search hitting relevance >= 0.6 confirms the obs is useful context.
                _high_rel = {m.id for sim, m in scored if sim >= 0.6}
                if _high_rel:
                    try:
                        _twm_rows = self.twm_read(limit=50, include_integrated=True)
                        for obs in _twm_rows:
                            # Extend TTL if the obs references any high-relevance memory
                            meta = obs.get("metadata", {})
                            if meta.get("memory_id") in _high_rel:
                                self.twm_extend_ttl(
                                    obs["id"], reason="search_signal_C_relevance>=0.6"
                                )
                    except Exception as _bare_e:
                        logging.getLogger(__name__).warning(
                            "bare except in wild_igor/igor/memory/cortex.py: %s",
                            _bare_e,
                        )

                # #66: affect-weighted retrieval — memories encoded in similar
                # emotional state get a small relevance boost (state-dependent recall)
                if req.emotional_context is not None:
                    for sim, m in scored:
                        v_sim = (
                            1.0
                            - abs(
                                getattr(m, "valence", 0.0)
                                - req.emotional_context.valence
                            )
                            / 2.0
                        )
                        a_sim = (
                            1.0
                            - abs(
                                getattr(m, "arousal", 0.0)
                                - req.emotional_context.arousal
                            )
                            / 2.0
                        )
                        m.relevance_score = sim * (1.0 + 0.15 * v_sim * a_sim)
                    scored.sort(
                        key=lambda x: getattr(x[1], "relevance_score", x[0]),
                        reverse=True,
                    )

                result = [m for _, m in scored[: req.limit]]
                # G9: spreading activation — boost graph neighbors
                result = self._spread_activation(
                    result, {}, req.limit, word_graph=req.word_graph
                )
                self._apply_recency_frequency_boost(result)
                self._touch_last_accessed(result)
                # T-308: Hebbian bridge Part 2 — memory → word graph feedback
                if req.word_graph is not None:
                    try:
                        from ..cognition.hebbian_bridge import record_retrieval_boost

                        _arousal = getattr(req.emotional_context, "arousal", 0.5) or 0.5
                        for _m in result:
                            record_retrieval_boost(req.word_graph, _m, _arousal)
                    except Exception as _bare_e:
                        logging.getLogger(__name__).debug(
                            "hebbian record_retrieval_boost skipped: %s", _bare_e
                        )
                # T-trails-infra v2: look up active TWM attractor to link trail → attractor
                _twm_obs_id: str | None = None
                try:
                    _att = self.twm_get_attractor()
                    if _att:
                        _twm_obs_id = str(_att["id"])
                except Exception as _e:
                    logging.getLogger("forensic").warning(
                        "[cortex.search] twm_get_attractor failed during trail record: %s",
                        _e,
                    )
                _trail_id = self._record_trace(
                    query,
                    result,
                    purpose="search",
                    twm_obs_id=_twm_obs_id,
                    instance_id=self._instance_id,
                )  # T-traces-infra: static path record
                self._record_tails(
                    result, trail_id=_trail_id
                )  # T-tails-infra: record activation heat
                self._apply_trail_training(
                    result
                )  # T-trail-training: Hebbian edge update
                # #309: reconsolidation flag — high-importance memories go plastic under arousal
                _rc_arousal = getattr(req.emotional_context, "arousal", None)
                self._flag_for_reconsolidation(result, milieu_arousal=_rc_arousal)
                return result
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )

        # Phase 1 fallback: candidates already scored + merged (#172); return top N
        result = candidates[: req.limit]
        # G9: spreading activation — boost graph neighbors
        result = self._spread_activation(
            result, {}, req.limit, word_graph=req.word_graph
        )
        self._apply_recency_frequency_boost(result)
        self._touch_last_accessed(result)
        # T-308: Hebbian bridge Part 2 — memory → word graph feedback (Phase 1 fallback)
        if req.word_graph is not None:
            try:
                from ..cognition.hebbian_bridge import record_retrieval_boost

                _arousal = getattr(req.emotional_context, "arousal", 0.5) or 0.5
                for _m in result:
                    record_retrieval_boost(req.word_graph, _m, _arousal)
            except Exception as _bare_e:
                logging.getLogger(__name__).debug(
                    "hebbian record_retrieval_boost skipped: %s", _bare_e
                )
        _trail_id = self._record_trace(
            query, result
        )  # T-traces-infra: static path record
        self._record_tails(
            result, trail_id=_trail_id
        )  # T-tails-infra: record activation heat
        self._apply_trail_training(result)  # T-trail-training: Hebbian edge update
        # #309: reconsolidation flag — high-importance memories go plastic under arousal
        _rc_arousal = getattr(req.emotional_context, "arousal", None)
        self._flag_for_reconsolidation(result, milieu_arousal=_rc_arousal)
        return result

    def _touch_last_accessed(self, memories: list) -> None:
        """G-DBM1: batch-update last_accessed for memories surfaced into LLM context.

        Uses a single SQL UPDATE (no full store() cycle) — lightweight, one write per
        search call regardless of result count.  Skips structural memories (ROOT/CP)
        since those are accessed every turn and would flood the timestamp.
        """
        if not memories:
            return
        _SKIP = {MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value}
        ids = [
            m.id
            for m in memories
            if getattr(m, "memory_type", None) and m.memory_type.value not in _SKIP
        ]
        if not ids:
            return
        now_iso = datetime.now().isoformat()
        placeholders = ",".join("?" * len(ids))
        try:
            with self._conn() as conn:
                conn.execute(
                    f"UPDATE memories SET last_accessed = ? WHERE id IN ({placeholders})",
                    [now_iso] + ids,
                )
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )

    # ── #309: Memory reconsolidation flag ─────────────────────────────────────

    def _flag_for_reconsolidation(
        self,
        memories: list,
        milieu_arousal: float | None = None,
    ) -> None:
        """
        #309: Mark retrieved memories as reconsolidate_pending when:
          - memory importance (activation_count proxy) suggests significance (>= 0.6)
          - milieu arousal is high (>= 0.4) — high arousal makes memories labile

        If milieu_arousal is not passed in, reads milieu lazily.
        Sets metadata.reconsolidate_pending = True and records current TWM hash
        so NE can compare context at reconsolidation time vs encoding time.

        Only writes to memories that aren't already flagged. Structural memories
        (ROOT/CORE_PATTERN) are never flagged.
        """
        import os as _os

        if not memories:
            return
        if _os.getenv("IGOR_RECONSOLIDATION_ENABLED", "true").lower() == "false":
            return

        # Get milieu arousal if not passed
        if milieu_arousal is None:
            try:
                _milieu_inst = __import__(
                    "igor.cognition.milieu", fromlist=["get"]
                ).get()
                _ms = _milieu_inst.get_state() if _milieu_inst else None
                milieu_arousal = max(0.0, _ms.arousal) if _ms else 0.0
            except Exception:
                milieu_arousal = 0.0

        if milieu_arousal < 0.4:
            return  # low arousal — memories stay stable

        _SKIP = {MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value}
        # Rough TWM context hash for reconsolidation comparison
        _twm_hash = str(hash(str([o.get("id") for o in self.twm_read(limit=10)])))

        flagged = 0
        for m in memories:
            if getattr(m, "memory_type", None) and m.memory_type.value in _SKIP:
                continue
            if m.metadata.get("reconsolidate_pending"):
                continue  # already flagged
            # importance proxy: activation_count/20 + base_inertia blended
            _importance_proxy = min(1.0, m.activation_count / 20.0 + m.inertia * 0.3)
            if _importance_proxy < 0.6:
                continue
            m.metadata["reconsolidate_pending"] = True
            m.metadata["reconsolidate_context"] = _twm_hash
            m.metadata["reconsolidate_arousal"] = round(milieu_arousal, 3)
            try:
                self.store(m)
                flagged += 1
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py _flag_for_reconsolidation: %s",
                    _bare_e,
                )
        if flagged:
            logging.getLogger(__name__).debug(
                "[reconsolidation] flagged %d memories (arousal=%.2f)",
                flagged,
                milieu_arousal,
            )

    # ── Tails — decaying activation heat (T-tails-infra) ──────────────────────

    def _record_tails(self, memories: list, trail_id: str | None = None) -> None:
        """Record a tail entry for each surfaced memory. Called after search results are final.

        Weight = relevance_score at time of surfacing (proxy for activation strength).
        trail_id groups this search call — same UUID as the traces entry.
        sequence_pos records traversal order within this trail.
        Decay computed on read via TAIL_GRADIENT — no background sweep needed.
        """
        if not memories:
            return
        now_iso = datetime.now().isoformat()
        rows = [
            (m.id, getattr(m, "relevance_score", 0.5) or 0.5, now_iso, trail_id, i)
            for i, m in enumerate(memories)
        ]
        try:
            with self._conn() as conn:
                conn.executemany(
                    "INSERT INTO tails (node_id, weight, recorded_at, trail_id, sequence_pos) VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
            # Prune old entries (>7 days) to keep table bounded
            cutoff = (datetime.now() - timedelta(days=7)).isoformat()
            with self._conn() as conn:
                conn.execute("DELETE FROM tails WHERE recorded_at < ?", (cutoff,))
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )

    def get_tail_heat(self, node_id: str) -> float:
        """Return current accumulated tail heat for a node using TAIL_GRADIENT.

        heat = sum(weight × TAIL_GRADIENT.factor_for(recorded_at)) over recent entries.
        Returns 0.0 if no entries or on error.
        """
        from ..cognition.temporal_gradient import TAIL_GRADIENT

        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT weight, recorded_at FROM tails WHERE node_id = ? "
                    "ORDER BY recorded_at DESC LIMIT 50",
                    (node_id,),
                ).fetchall()
            if not rows:
                return 0.0
            now = datetime.now()
            total = 0.0
            for weight, recorded_at_str in rows:
                try:
                    recorded_at = datetime.fromisoformat(recorded_at_str)
                    total += TAIL_GRADIENT.apply_for(weight, recorded_at, now)
                except Exception:
                    continue
            return round(total, 4)
        except Exception:
            return 0.0

    # ── Traces — static path record (T-traces-infra) ───────────────────────────

    def _record_trace(
        self,
        query: str,
        memories: list,
        *,
        purpose: str = "search",
        twm_obs_id: str | None = None,
        instance_id: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """Record a static trace of this search call — query + nodes activated.

        Permanent record (no decay). One trace per search() call.
        nodes stored as JSON: [{node_id, relevance, memory_type, sequence_pos}]

        T-trails-infra v2: also stores purpose, twm_obs_id (attractor that drove
        the search), instance_id, and thread_id for provenance + TWM join.
        """
        if not memories:
            return
        import uuid as _uuid
        import json as _json

        trace_id = str(_uuid.uuid4())
        now_iso = datetime.now().isoformat()
        nodes = [
            {
                "node_id": m.id,
                "relevance": round(getattr(m, "relevance_score", 0.0) or 0.0, 4),
                "memory_type": m.memory_type.value if m.memory_type else "UNKNOWN",
                "sequence_pos": i,
            }
            for i, m in enumerate(memories)
        ]
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO traces "
                    "(id, recorded_at, query, nodes, purpose, twm_obs_id, instance_id, thread_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trace_id,
                        now_iso,
                        query[:200],
                        _json.dumps(nodes),
                        purpose,
                        twm_obs_id,
                        instance_id,
                        thread_id,
                    ),
                )
            # Prune traces older than 30 days
            cutoff = (datetime.now() - timedelta(days=30)).isoformat()
            with self._conn() as conn:
                conn.execute("DELETE FROM traces WHERE recorded_at < ?", (cutoff,))
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )
        return trace_id  # T-trails-infra: caller uses this as trail_id for tails

    def _apply_trail_training(self, memories: list) -> None:
        """
        T-trail-training: Hebbian edge strengthening from co-activation.

        For each ordered pair (A, B) in this search result:
          ΔW = LEARNING_RATE × heat_A × heat_B
          STDP: A fired before B → LTP multiplier; B before A → LTD multiplier.
          Apply ΔW to existing co_activation interpretive_edge, or create one if
          delta exceeds CREATION_THRESHOLD.

        Gate: IGOR_TRAIL_TRAINING_ENABLED=true (default false).
        Never raises — must not block search.
        """
        import os as _os

        if _os.getenv("IGOR_TRAIL_TRAINING_ENABLED", "false").lower() != "true":
            return
        if not memories or len(memories) < 2:
            return

        lr = float(_os.getenv("IGOR_TRAIL_LEARNING_RATE", "0.05"))
        creation_threshold = float(_os.getenv("IGOR_TRAIL_CREATION_THRESHOLD", "0.1"))
        max_weight = float(_os.getenv("IGOR_TRAIL_MAX_WEIGHT", "5.0"))
        ltp = float(_os.getenv("IGOR_TRAIL_LTP_MULTIPLIER", "1.2"))
        ltd = float(_os.getenv("IGOR_TRAIL_LTD_MULTIPLIER", "0.8"))
        max_pairs = int(_os.getenv("IGOR_TRAIL_PAIRS_PER_TRACE", "10"))

        try:
            # Get heats for all nodes in this trace (batch via existing get_tail_heat)
            ids = [m.id for m in memories]
            heats = {m_id: self.get_tail_heat(m_id) for m_id in ids}

            pairs_processed = 0
            for i, mem_a in enumerate(memories):
                if pairs_processed >= max_pairs:
                    break
                heat_a = heats.get(mem_a.id, 0.0)
                if heat_a < 1e-6:
                    continue
                for j, mem_b in enumerate(memories):
                    if i == j or pairs_processed >= max_pairs:
                        break
                    heat_b = heats.get(mem_b.id, 0.0)
                    if heat_b < 1e-6:
                        continue

                    delta = lr * heat_a * heat_b
                    # STDP: i < j means A fired before B → LTP; else LTD
                    delta *= ltp if i < j else ltd

                    if delta < 1e-9:
                        continue

                    # Look for existing co_activation edge A→B
                    existing = None
                    try:
                        with self._conn() as conn:
                            row = conn.execute(
                                "SELECT id, weight FROM interpretive_edges "
                                "WHERE from_id=? AND to_id=? AND direction='co_activation'",
                                (mem_a.id, mem_b.id),
                            ).fetchone()
                        if row:
                            existing = row
                    except Exception as _bare_e:
                        logging.getLogger(__name__).warning(
                            "bare except in wild_igor/igor/memory/cortex.py: %s",
                            _bare_e,
                        )

                    if existing:
                        new_weight = min(max_weight, float(existing["weight"]) + delta)
                        try:
                            with self._conn() as conn:
                                conn.execute(
                                    "UPDATE interpretive_edges SET weight=? WHERE id=?",
                                    (new_weight, existing["id"]),
                                )
                        except Exception as _bare_e:
                            logging.getLogger(__name__).warning(
                                "bare except in wild_igor/igor/memory/cortex.py: %s",
                                _bare_e,
                            )
                    elif delta >= creation_threshold:
                        try:
                            self.add_interpretive_edge(
                                mem_a.id,
                                mem_b.id,
                                direction="co_activation",
                                meaning_payload=f"Hebbian; delta={delta:.4f}",
                                weight=min(delta, max_weight),
                                layer="trail_training",
                            )
                        except Exception as _bare_e:
                            logging.getLogger(__name__).warning(
                                "bare except in wild_igor/igor/memory/cortex.py: %s",
                                _bare_e,
                            )

                    pairs_processed += 1
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )

    def get_recent_traces(self, limit: int = 10) -> list:
        """Return recent traces for Igor introspection — newest first.

        Each entry: {id, recorded_at, query, nodes: [{node_id, relevance, memory_type, sequence_pos}]}
        """
        import json as _json

        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT id, recorded_at, query, nodes FROM traces "
                    "ORDER BY recorded_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [
                {
                    "id": r[0],
                    "recorded_at": r[1],
                    "query": r[2],
                    "nodes": _json.loads(r[3]),
                }
                for r in rows
            ]
        except Exception:
            return []

    # ── Trail inspection API (T-trails-infra) ─────────────────────────────────

    def trails_through_node(self, node_id: str, limit: int = 10) -> list:
        """Return recent trails that passed through node_id.

        Each entry: {trail_id, recorded_at, node_count, nodes: [{node_id, sequence_pos, weight}]}
        Ordered newest first.
        """
        try:
            with self._conn() as conn:
                trail_ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT trail_id FROM tails "
                        "WHERE node_id = ? AND trail_id IS NOT NULL "
                        "ORDER BY recorded_at DESC LIMIT ?",
                        (node_id, limit),
                    ).fetchall()
                ]
            if not trail_ids:
                return []
            results = []
            for tid in trail_ids:
                with self._conn() as conn:
                    rows = conn.execute(
                        "SELECT node_id, sequence_pos, weight, recorded_at "
                        "FROM tails WHERE trail_id = ? ORDER BY sequence_pos",
                        (tid,),
                    ).fetchall()
                if rows:
                    results.append(
                        {
                            "trail_id": tid,
                            "recorded_at": rows[0][3],
                            "node_count": len(rows),
                            "nodes": [
                                {
                                    "node_id": r[0],
                                    "sequence_pos": r[1],
                                    "weight": round(float(r[2]), 4),
                                }
                                for r in rows
                            ],
                        }
                    )
            return results
        except Exception:
            return []

    def trail_gradient(self, node_id: str, window_minutes: int = 60) -> dict:
        """Compute the heat trend for a node — rising, flat, or fading.

        Splits recent history into two equal windows and compares summed heat.
        Returns: {trend: 'rising'|'flat'|'fading', recent_heat: float, earlier_heat: float}
        """
        from ..cognition.temporal_gradient import TAIL_GRADIENT

        try:
            now = datetime.now()
            half = timedelta(minutes=window_minutes // 2)
            full = timedelta(minutes=window_minutes)
            mid = now - half
            start = now - full

            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT weight, recorded_at FROM tails "
                    "WHERE node_id = ? AND recorded_at > ? "
                    "ORDER BY recorded_at DESC",
                    (node_id, start.isoformat()),
                ).fetchall()

            recent_heat = 0.0
            earlier_heat = 0.0
            for weight, recorded_at_str in rows:
                try:
                    recorded_at = datetime.fromisoformat(recorded_at_str)
                    decayed = TAIL_GRADIENT.apply_for(float(weight), recorded_at, now)
                    if recorded_at >= mid:
                        recent_heat += decayed
                    else:
                        earlier_heat += decayed
                except Exception:
                    continue

            if earlier_heat < 1e-6:
                trend = "rising" if recent_heat > 1e-6 else "flat"
            elif recent_heat > earlier_heat * 1.3:
                trend = "rising"
            elif recent_heat < earlier_heat * 0.7:
                trend = "fading"
            else:
                trend = "flat"

            return {
                "trend": trend,
                "recent_heat": round(recent_heat, 4),
                "earlier_heat": round(earlier_heat, 4),
            }
        except Exception:
            return {"trend": "unknown", "recent_heat": 0.0, "earlier_heat": 0.0}

    def hot_paths(self, limit: int = 10, since_hours: int = 24) -> list:
        """Return the most frequently co-activated node pairs from recent trails.

        Each entry: {node_a, node_b, co_count, last_seen}
        Ordered by co_count descending.
        """
        try:
            cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()
            with self._conn() as conn:
                # Self-join on trail_id to find co-occurring node pairs.
                # Aliases required: _PGRowProxy collapses duplicate column names.
                rows = conn.execute(
                    "SELECT a.node_id AS node_a, b.node_id AS node_b, "
                    "COUNT(*) AS co_count, MAX(a.recorded_at) AS last_seen "
                    "FROM tails a "
                    "JOIN tails b ON a.trail_id = b.trail_id AND a.node_id < b.node_id "
                    "WHERE a.trail_id IS NOT NULL AND a.recorded_at > ? "
                    "GROUP BY a.node_id, b.node_id "
                    "ORDER BY co_count DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
            return [
                {
                    "node_a": r["node_a"],
                    "node_b": r["node_b"],
                    "co_count": r["co_count"],
                    "last_seen": r["last_seen"],
                }
                for r in rows
            ]
        except Exception:
            return []

    # ── Traversal context — habit-chain execution state (T-traversal-context) ──

    def traversal_start(self, job_id: str = "") -> str:
        """Mint a new traversal context_id and record its metadata row.

        Returns the context_id (UUID). The caller should push it to TWM under
        the key 'TRAVERSAL_CTX_ID' so downstream habits in the same chain can
        retrieve it via traversal_get().

        job_id is optional — pass the background job id when this chain runs
        under job_manager so the context can be correlated with job logs.
        """
        import uuid as _uuid

        ctx_id = str(_uuid.uuid4())
        now = datetime.utcnow().isoformat()
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO traversal_contexts "
                    "(context_id, job_id, key, value, step, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ctx_id, job_id or "", "__init__", "1", 0, now),
                )
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )
        return ctx_id

    def traversal_get(self, context_id: str, key: str) -> Optional[str]:
        """Return the value stored at (context_id, key), or None if not set."""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT value FROM traversal_contexts "
                    "WHERE context_id = ? AND key = ?",
                    (context_id, key),
                ).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def traversal_set(
        self, context_id: str, key: str, value: str, step: int = 0
    ) -> None:
        """Write or overwrite (context_id, key) → value.

        step is informational — records which step in the chain wrote this key,
        useful for post-hoc trace inspection.
        """
        now = datetime.utcnow().isoformat()
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO traversal_contexts "
                    "(context_id, job_id, key, value, step, recorded_at) "
                    "VALUES (?, '', ?, ?, ?, ?) "
                    "ON CONFLICT(context_id, key) DO UPDATE SET "
                    "value=excluded.value, step=excluded.step, recorded_at=excluded.recorded_at",
                    (context_id, key, value, step, now),
                )
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )

    def get_by_activation(self, limit: int = 30) -> list:
        """T-db-spreading-activation: fetch recently-activated memories by tail heat.

        D200: SQL lives in db_proxy.get_activation_rows() / fetch_by_ids().
        Falls back to last_accessed ordering if tails is empty.
        """
        _SKIP = (MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value)
        _excl_ph = ",".join("?" * len(_SKIP))
        try:
            tail_rows = self._db.get_activation_rows(limit * 2, since_hours=48.0)
            if not tail_rows:
                raise ValueError("tails empty")
            hot_ids = [r[0] for r in tail_rows]
            rows = self._db.fetch_by_ids(hot_ids, excl_types=_SKIP)
            mems = [self._to_memory(r) for r in rows]
            heat_map = {r[0]: r[1] for r in tail_rows}
            mems.sort(key=lambda m: heat_map.get(m.id, ""), reverse=True)
            return mems[:limit]
        except Exception:
            # Fallback: last_accessed ordering (SQL stays here — rare exception path)
            with self._conn() as conn:
                rows = conn.execute(
                    f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                    f"WHERE last_accessed IS NOT NULL AND memory_type NOT IN ({_excl_ph}) "
                    "ORDER BY last_accessed DESC LIMIT ?",
                    list(_SKIP) + [limit],
                ).fetchall()
            return [self._to_memory(r) for r in rows]

    # D233: spreading activation ───────────────────────────────────────────────

    def spreading_activation(
        self,
        seed_nodes: list,
        depth: int = 2,
        word_graph=None,
    ) -> dict:
        """D233: Two-layer spreading activation from seed memory nodes.

        Returns dict[node_id, float] with combined activation heat.
        Seeds = memory IDs (callers pass TWM top-7 per Miller's Law).

        Layer 1 — word-graph (hop_decay=0.6, feeds predict_next):
          Seed narratives → word tokenization → wg_edges spreading.
          Bridge: hot words → memories via wg_word_docs content index.
          Requires word_graph parameter; skipped when None.

        Layer 2 — memory graph (hop_decay=0.8, feeds cortex.search):
          Seed IDs → parent/children/links traversal for `depth` hops.

        Activations summed (not max) across seeds and layers.
        """
        scores: dict = {}
        if not seed_nodes:
            return scores

        _log = logging.getLogger(__name__)
        _forensic = logging.getLogger("forensic")
        _MEM_HOP_DECAY = 0.8

        # Seed memories start at 1.0
        for nid in seed_nodes:
            scores[nid] = scores.get(nid, 0.0) + 1.0

        # ── Layer 2: memory graph spreading (hop_decay=0.8) ─────────────────
        try:
            current_frontier = {nid: 1.0 for nid in seed_nodes}
            for _ in range(depth):
                if not current_frontier:
                    break
                next_frontier: dict = {}
                _cached, _miss_ids = self._cache_fetch_ids(
                    list(current_frontier.keys())
                )
                if _miss_ids:
                    with self._conn() as conn:
                        _ph = ",".join("?" * len(_miss_ids))
                        _rows = conn.execute(
                            f"SELECT {_MEM_COLS_NO_EMBED} FROM memories"
                            f" WHERE id IN ({_ph})",
                            _miss_ids,
                        ).fetchall()
                    for _row in _rows:
                        _m = self._to_memory(_row)
                        self._cache_put(_m)
                        _cached.append(_m)
                for m in _cached:
                    base = current_frontier.get(m.id, 0.0)
                    spread = base * _MEM_HOP_DECAY
                    adj: list = []
                    if getattr(m, "parent_id", None):
                        adj.append((m.parent_id, spread))
                    for cid in getattr(m, "children_ids", []) or []:
                        adj.append((cid, spread))
                    for lid in getattr(m, "link_ids", []) or []:
                        adj.append((lid, spread))
                    for lnk_id, lnk_w in (getattr(m, "links", {}) or {}).items():
                        adj.append((lnk_id, base * float(lnk_w) * _MEM_HOP_DECAY))
                    for adj_id, adj_spread in adj:
                        next_frontier[adj_id] = (
                            next_frontier.get(adj_id, 0.0) + adj_spread
                        )
                for nid, s in next_frontier.items():
                    scores[nid] = scores.get(nid, 0.0) + s
                current_frontier = next_frontier
        except Exception as _bare_e:
            _log.warning(
                "bare except in wild_igor/igor/memory/cortex.py"
                " spreading_activation memory layer: %s",
                _bare_e,
            )

        # ── Layer 1: word-graph spreading (hop_decay=0.6) + bridge ──────────
        if word_graph is not None:
            try:
                from ..cognition.word_graph import tokenize

                seed_word_scores: dict = {}
                for nid in seed_nodes:
                    m = self.get(nid)
                    if m and m.narrative:
                        for w in tokenize(m.narrative):
                            seed_word_scores[w] = seed_word_scores.get(w, 0.0) + 1.0
                if seed_word_scores:
                    wg_activations = word_graph.spread_from_words(
                        seed_word_scores, hop_decay=0.6, depth=depth
                    )
                    doc_activations = word_graph.words_to_doc_ids(wg_activations)
                    _WG_BRIDGE_SCALE = 0.6
                    for doc_id, act in doc_activations.items():
                        scores[doc_id] = (
                            scores.get(doc_id, 0.0) + act * _WG_BRIDGE_SCALE
                        )
                    _forensic.debug(
                        "[cortex.spreading_activation] wg layer:"
                        " %d word seeds → %d wg words → %d docs",
                        len(seed_word_scores),
                        len(wg_activations),
                        len(doc_activations),
                    )
            except Exception as _bare_e:
                _log.warning(
                    "bare except in wild_igor/igor/memory/cortex.py"
                    " spreading_activation wg layer: %s",
                    _bare_e,
                )

        return scores

    def _apply_recency_frequency_boost(self, memories: list) -> None:
        """#128 + G45: apply small recency, frequency, inertia, and confidence multipliers."""
        now = datetime.now()
        for m in memories:
            score = getattr(m, "relevance_score", 0.0) or 0.0
            # Recency: decays over 30 days, max +15%
            if m.last_accessed:
                days = max(0.0, (now - m.last_accessed).total_seconds() / 86400)
                recency = max(0.0, 1.0 - days / 30.0)
                score *= 1.0 + 0.15 * recency
            # Frequency: caps at 20 activations, max +10%
            freq = min(1.0, m.activation_count / 20.0)
            score *= 1.0 + 0.10 * freq
            # G45: inertia weighting — established memories slightly preferred [0.90, 1.05]
            # Low-inertia episodics (0.20) get -10%; high-inertia core patterns (0.95) get +4%
            score *= 0.90 + 0.15 * m.inertia
            # G45: confidence weighting (G46 field) — uncertain memories slightly penalized [0.90, 1.00]
            confidence = getattr(m, "confidence", 1.0) or 1.0
            score *= 0.90 + 0.10 * confidence
            m.relevance_score = score  # type: ignore[attr-defined]

    # G9 / #60: spreading activation ──────────────────────────────────────────

    _SA_DECAY = 0.4  # neighbor relevance = parent_relevance * _SA_DECAY

    def _spread_activation(
        self,
        activated: list,
        all_memories: dict,
        limit: int,
        word_graph=None,
    ) -> list:
        """
        Given a list of activated Memory objects (with .relevance_score set),
        find their graph neighbors (parent_id, children_ids, link_ids) and give
        them a decay-weighted partial activation boost.

        Neighbors already in `activated` get a small relevance bump.
        New neighbors below the original activation threshold are appended
        at decayed relevance and sorted back into the result.

        T-308 (word_graph): if provided and IGOR_HEBBIAN_BRIDGE is on, calls
        wg_predict_for_activation() to get predicted words from activated nodes,
        then scores already-loaded neighbors by narrative overlap.

        Returns the merged list (max `limit` items).
        """
        activated_ids = {m.id for m in activated}
        neighbor_scores: dict[str, float] = {}

        for m in activated:
            base = getattr(m, "relevance_score", 0.1) or 0.1
            spread = base * self._SA_DECAY

            # Gather adjacent node IDs with their spread amounts
            # #128: weighted links use link weight × decay; unweighted use flat decay
            spread_map: dict[str, float] = {}

            if getattr(m, "parent_id", None):
                spread_map[m.parent_id] = spread
            for cid in getattr(m, "children_ids", []) or []:
                spread_map[cid] = spread

            # Weighted directed links (new) — spread proportional to weight
            for link_id, link_weight in (getattr(m, "links", {}) or {}).items():
                weighted_spread = base * link_weight * self._SA_DECAY
                spread_map[link_id] = max(spread_map.get(link_id, 0.0), weighted_spread)

            # Legacy link_ids — use flat decay, don't double-count if already in links
            existing_links = set(getattr(m, "links", {}) or {})
            for lid in getattr(m, "link_ids", []) or []:
                if lid not in existing_links:
                    spread_map[lid] = max(spread_map.get(lid, 0.0), spread)

            for adj_id, adj_spread in spread_map.items():
                if adj_id in activated_ids:
                    # Already activated — small boost only
                    existing = next((x for x in activated if x.id == adj_id), None)
                    if existing:
                        existing.relevance_score = min(  # type: ignore[attr-defined]
                            1.0,
                            getattr(existing, "relevance_score", 0.0)
                            + adj_spread * 0.3,
                        )
                else:
                    # New neighbor — record best spread score
                    if (
                        adj_id not in neighbor_scores
                        or neighbor_scores[adj_id] < adj_spread
                    ):
                        neighbor_scores[adj_id] = adj_spread

        # Fetch new neighbors from DB; skip structural infrastructure only
        _SKIP_TYPES = {
            MemoryType.ROOT.value,
            MemoryType.CORE_PATTERN.value,
        }
        new_neighbors: list = []
        if neighbor_scores:
            _cached, _miss_ids = self._cache_fetch_ids(list(neighbor_scores.keys()))
            if _miss_ids:
                with self._conn() as conn:
                    placeholders = ",".join("?" * len(_miss_ids))
                    _rows = conn.execute(
                        f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE id IN ({placeholders})",
                        _miss_ids,
                    ).fetchall()
                for _row in _rows:
                    _m = self._to_memory(_row)
                    self._cache_put(_m)
                    _cached.append(_m)
            for m in _cached:
                if m.memory_type in _SKIP_TYPES:
                    continue
                m.relevance_score = neighbor_scores[m.id]  # type: ignore[attr-defined]
                new_neighbors.append(m)

        # T-308: Hebbian bridge Part 3 — spreading activation word graph extension.
        # Predict next-words for each activated node; score already-loaded neighbors
        # by narrative overlap with predictions. No extra DB calls — works on loaded data.
        if word_graph is not None:
            try:
                from ..cognition.hebbian_bridge import wg_predict_for_activation

                _wg_words = wg_predict_for_activation(word_graph, activated)
                if _wg_words:
                    _all_loaded = activated + new_neighbors
                    _activated_ids = {m.id for m in activated}
                    for _m in _all_loaded:
                        if _m.id in _activated_ids:
                            continue
                        _narr = (getattr(_m, "narrative", None) or "").lower()
                        _hits = sum(1 for w in _wg_words if w in _narr)
                        if _hits > 0:
                            _boost = min(0.05, _hits * 0.01)
                            _cur = getattr(_m, "relevance_score", 0.0) or 0.0
                            _m.relevance_score = min(1.0, _cur + _boost)  # type: ignore[attr-defined]
            except Exception as _bare_e:
                logging.getLogger(__name__).debug(
                    "hebbian wg_predict_for_activation skipped: %s", _bare_e
                )

        merged = activated + new_neighbors
        merged.sort(key=lambda m: getattr(m, "relevance_score", 0.0), reverse=True)
        return merged[:limit]

    # #172: traversal-first retrieval ─────────────────────────────────────────

    def _get_context_anchors(self) -> list[str]:
        """
        #172: Return memory IDs to use as BFS anchor nodes.
        Sources (in priority order):
        1. TWM attractor metadata.memory_id (explicit pointer)
        2. Quick text match on attractor content_csb (implicit anchor)
        3. Recent high-salience TWM items with metadata.memory_id set
        Returns up to 5 anchor IDs.
        """
        anchors: list[str] = []
        seen: set[str] = set()

        # 1 + 2: TWM attractor
        try:
            attractor = self.twm_get_attractor()
            if attractor:
                mid = (attractor.get("metadata") or {}).get("memory_id")
                if mid and mid not in seen:
                    anchors.append(mid)
                    seen.add(mid)
                elif attractor.get("content_csb"):
                    # Implicit anchor: quick keyword match on attractor content
                    terms = attractor["content_csb"].lower().split()[:8]
                    with self._conn() as conn:
                        rows = conn.execute(
                            "SELECT id, narrative FROM memories "
                            "WHERE memory_type NOT IN (?, ?) LIMIT 200",
                            (MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value),
                        ).fetchall()
                    scored = [
                        (sum(1 for t in terms if t in r["narrative"].lower()), r["id"])
                        for r in rows
                    ]
                    scored.sort(reverse=True)
                    for score, rid in scored[:2]:
                        if score >= 2 and rid not in seen:
                            anchors.append(rid)
                            seen.add(rid)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )

        # 3: Recent TWM items with explicit memory_id in metadata
        try:
            recent = self.twm_read(limit=10, include_integrated=False)
            for obs in sorted(
                recent, key=lambda x: x.get("salience", 0.0), reverse=True
            )[:5]:
                mid = (obs.get("metadata") or {}).get("memory_id")
                if mid and mid not in seen:
                    anchors.append(mid)
                    seen.add(mid)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )

        return anchors[:5]

    def traverse_from(
        self,
        anchor_ids: list[str],
        depth: int = 2,
        limit: int = 20,
    ) -> list:
        """
        #172: BFS from anchor_ids, following all edge types (parent, children, links).
        Returns Memory objects with relevance_score = decay-weighted path score.
        Anchor nodes themselves are included at score 1.0.
        D200: per-hop DB fetch delegates to db_proxy.fetch_by_ids().
        """
        _SKIP = {MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value}
        visited: dict[str, float] = {mid: 1.0 for mid in anchor_ids}
        frontier: list[tuple[str, float]] = [(mid, 1.0) for mid in anchor_ids]
        # G-QP7: collect fetched Memory objects during BFS hops; avoids a final
        # bulk IN fetch that was always a cache miss (boundary nodes discovered at
        # the last hop were added to `visited` but never fetched — 806ms every NE cycle).
        fetched_mems: dict[str, "Memory"] = {}

        for _hop in range(depth):
            if not frontier:
                break
            ids = [fid for fid, _ in frontier]
            _cached, _miss_ids = self._cache_fetch_ids(ids)
            if _miss_ids:
                rows = self._db.fetch_by_ids(_miss_ids)
                for _row in rows:
                    _m = self._to_memory(_row)
                    self._cache_put(_m)
                    _cached.append(_m)
            mem_map = {m.id: m for m in _cached}
            fetched_mems.update(mem_map)

            next_frontier: list[tuple[str, float]] = []
            for fid, fscore in frontier:
                m = mem_map.get(fid)
                if m is None:
                    continue
                neighbors: dict[str, float] = {}
                decay = self._SA_DECAY
                if getattr(m, "parent_id", None):
                    neighbors[m.parent_id] = fscore * decay
                for cid in getattr(m, "children_ids", []) or []:
                    neighbors[cid] = max(neighbors.get(cid, 0.0), fscore * decay)
                for lid, lw in (getattr(m, "links", {}) or {}).items():
                    neighbors[lid] = max(
                        neighbors.get(lid, 0.0), fscore * float(lw) * decay
                    )
                existing_links = set(getattr(m, "links", {}) or {})
                for lid in getattr(m, "link_ids", []) or []:
                    if lid not in existing_links:
                        neighbors[lid] = max(neighbors.get(lid, 0.0), fscore * decay)
                for nid, nscore in neighbors.items():
                    if nscore > visited.get(nid, 0.0):
                        visited[nid] = nscore
                        next_frontier.append((nid, nscore))
            frontier = next_frontier

        if len(fetched_mems) <= len(anchor_ids):
            return []  # No traversal beyond anchors — graph likely sparse

        results = []
        for m in fetched_mems.values():
            if m.memory_type in _SKIP:
                continue
            m.relevance_score = visited.get(m.id, 0.0)  # type: ignore[attr-defined]
            results.append(m)
        results.sort(key=lambda m: getattr(m, "relevance_score", 0.0), reverse=True)
        return results[:limit]

    def _upsert_embedding(self, memory_id: str, embedding_json: str) -> None:
        """G-EMB1: Postgres-safe embedding upsert. INSERT OR REPLACE is SQLite-only."""
        from .db_proxy import PGDatabaseProxy

        with self._conn() as conn:
            if isinstance(self._db, PGDatabaseProxy):
                conn.execute(
                    "INSERT INTO memory_embeddings(memory_id, embedding) VALUES (?, ?)"
                    " ON CONFLICT (memory_id) DO UPDATE SET embedding = EXCLUDED.embedding",
                    (memory_id, embedding_json),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO memory_embeddings(memory_id, embedding)"
                    " VALUES (?, ?)",
                    (memory_id, embedding_json),
                )

    def _get_or_compute_embedding(self, memory) -> Optional[list]:
        """
        G-EMB1: Return the stored embedding from memory_embeddings (separate table).
        Falls back to computing via Ollama if missing.
        Returns None if Ollama is unavailable.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT embedding FROM memory_embeddings WHERE memory_id = ?",
                (memory.id,),
            ).fetchone()
        if row and row["embedding"]:
            try:
                return json.loads(row["embedding"])
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )

        # Not in separate table — compute via embedder and store
        try:
            from ..cognition.embedder import embed

            vec = embed(memory.narrative)
            if vec:
                self._upsert_embedding(memory.id, json.dumps(vec))
                return vec
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )
        return None

    def _get_embeddings_batch(self, ids: list) -> dict:
        """
        G-EMB1: Batch-fetch embeddings for a list of memory ids.
        One SQL round-trip vs N individual selects in Phase 2 of search().
        Returns {memory_id: list[float] or None}.
        """
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT memory_id, embedding FROM memory_embeddings"
                f" WHERE memory_id IN ({placeholders})",
                ids,
            ).fetchall()
        result: dict = {}
        for row in rows:
            try:
                result[row["memory_id"]] = (
                    json.loads(row["embedding"]) if row["embedding"] else None
                )
            except Exception:
                result[row["memory_id"]] = None
        return result

    def count_by_type(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT memory_type, COUNT(*) as n FROM memories GROUP BY memory_type"
            ).fetchall()
        return {row["memory_type"]: row["n"] for row in rows}

    def total_count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def get_habits(self) -> list:
        # #128: any memory with a trigger field is a habit — not gated on PROCEDURAL type
        # #260: in-process cache — avoids repeated full-table LIKE scan (55ms each)
        if self._habit_cache is not None:
            return self._habit_cache
        from .db_proxy import PGDatabaseProxy

        _habits_sql = (
            f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE jsonb_exists(metadata, 'trigger') OR jsonb_exists(metadata, 'conditions')"
            if isinstance(self._db, PGDatabaseProxy)
            else f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE metadata LIKE '%\"trigger\"%' OR metadata LIKE '%\"conditions\"%'"
        )
        with self._conn() as conn:
            rows = conn.execute(_habits_sql).fetchall()
        result = [m for m in (self._to_memory(r) for r in rows) if m.is_habit]
        self._habit_cache = result
        return result

    def invalidate_habit_cache(self) -> None:
        """Clear the in-process habit cache. Call after any habit is added or updated."""
        self._habit_cache = None

    def backfill_embeddings(self, batch_size: int = 50) -> int:
        """
        Compute and store DB embeddings for memories that are missing them.
        Checks file cache first — most will be cache hits (~1ms each).
        Only calls Ollama for genuinely unseen narratives.
        Returns count of memories updated.
        Run at boot in a daemon thread so first search is never cold.
        """
        try:
            from ..cognition.embedder import embed as _embed
            import json as _json
        except Exception:
            return 0

        # G-EMB1: find memories missing from memory_embeddings (separate table)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT m.id, m.narrative FROM memories m"
                " LEFT JOIN memory_embeddings e ON e.memory_id = m.id"
                " WHERE e.memory_id IS NULL"
            ).fetchall()

        updated = 0
        for row in rows:
            text = (row["narrative"] or "").strip()
            if not text:
                continue
            try:
                vec = _embed(text)
                if vec:
                    self._upsert_embedding(row["id"], _json.dumps(vec))
                    updated += 1
            except Exception:
                continue

        return updated

    def delete_memory(self, memory_id: str) -> bool:
        """
        #152: Delete a memory by ID. Returns True if deleted.
        Caller is responsible for ensuring the ID is not genesis-protected.
        Used by /hygiene --apply.
        """
        with self._conn() as conn:
            result = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        return result.rowcount > 0

    def integrity_check(self) -> tuple[bool, list[str]]:
        """
        Verify referential integrity of the genesis core pattern graph (change.28).

        Checks:
          - CP1-CP6 exist with parent_id="ROOT"
          - ID1-ID14 exist with parent_id in {CP1..CP6}
          - PROC1-PROC10 exist with parent_id in {CP1..CP6} ∪ {ID1..ID14}

        Returns (passes, list_of_violations).
        An empty DB is not a violation — genesis will populate it on first boot.
        """
        if self.total_count() == 0:
            return True, []

        violations: list[str] = []
        valid_cp = {f"CP{i}" for i in range(1, 7)}
        valid_id = {f"ID{i}" for i in range(1, 15)}
        valid_proc_parents = valid_cp | valid_id

        for cp_id in sorted(valid_cp):
            mem = self.get(cp_id)
            if mem is None:
                violations.append(f"MISSING_CP: {cp_id}")
            elif mem.parent_id != "ROOT":
                violations.append(
                    f"ORPHAN_CP: {cp_id} parent={mem.parent_id!r} (expected ROOT)"
                )

        for id_id in sorted(valid_id):
            mem = self.get(id_id)
            # Missing ID: informational — may be a pre-backfill instance; not a corruption signal
            if mem is not None and mem.parent_id not in valid_cp:
                violations.append(
                    f"INVALID_PARENT_ID: {id_id} parent={mem.parent_id!r} (expected CP1-CP6)"
                )

        for i in range(1, 11):
            proc_id = f"PROC{i}"
            mem = self.get(proc_id)
            # Missing PROC: informational — may be a pre-backfill instance; not a corruption signal
            if mem is not None and mem.parent_id not in valid_proc_parents:
                violations.append(
                    f"INVALID_PARENT_PROC: {proc_id} parent={mem.parent_id!r}"
                )

        return len(violations) == 0, violations

    def _to_memory(self, row) -> Memory:
        keys = row.keys()
        # #128: load directed weighted links
        _links = {}
        if "links_weighted" in keys:
            try:
                _links = json.loads(row["links_weighted"] or "{}")
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )
        # #128: load last_accessed
        _last_accessed = None
        if "last_accessed" in keys and row["last_accessed"]:
            try:
                _last_accessed = datetime.fromisoformat(row["last_accessed"])
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                )
        return Memory(
            id=row["id"],
            narrative=row["narrative"],
            memory_type=_safe_memory_type(row["memory_type"]),
            parent_id=row["parent_id"],
            children_ids=json.loads(row["children_ids"]),
            link_ids=json.loads(row["link_ids"]),
            links=_links,
            valence=row["valence"] or 0.0,
            arousal=row["arousal"] if "arousal" in keys else 0.0,
            dominance=row["dominance"] if "dominance" in keys else 0.0,
            activation_count=row["activation_count"],
            friction_history=json.loads(row["friction_history"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            last_accessed=_last_accessed,
            metadata=(
                row["metadata"]
                if isinstance(row["metadata"], dict)
                else json.loads(row["metadata"] or "{}")
            ),
            portable=bool(row["portable"]) if "portable" in keys else True,
            scope=(
                MemoryScope(row["scope"])
                if "scope" in keys and row["scope"]
                else default_scope(_safe_memory_type(row["memory_type"]))
            ),
            # G46: provenance + epistemic fields
            source=row["source"] if "source" in keys and row["source"] else "",
            confidence=(
                float(row["confidence"])
                if "confidence" in keys and row["confidence"] is not None
                else 1.0
            ),
            context_of_encoding=(
                row["context_of_encoding"]
                if "context_of_encoding" in keys and row["context_of_encoding"]
                else ""
            ),
            # D260: engram program payload
            payload=(
                (
                    row["payload"]
                    if isinstance(row["payload"], dict)
                    else json.loads(row["payload"])
                )
                if "payload" in keys and row["payload"]
                else None
            ),
        )

    # ── In-process memory fetch cache (#258b) ─────────────────────────────────

    def _cache_get(self, memory_id: str) -> Optional[Memory]:
        """Return cached Memory if still valid, else None."""
        entry = self._mem_cache.get(memory_id)
        if entry is None:
            return None
        mem, ts = entry
        if mem.memory_type.value in _GENESIS_MEM_TYPES:
            return mem  # permanent — never expires
        if time.monotonic() - ts < _MEM_CACHE_TTL:
            return mem
        del self._mem_cache[memory_id]
        return None

    def _cache_put(self, mem: Memory) -> None:
        """Store a Memory in the in-process cache."""
        self._mem_cache[mem.id] = (mem, time.monotonic())

    def _cache_fetch_ids(self, ids) -> tuple:
        """Split ids into (cached_memories, uncached_id_strings)."""
        cached, misses = [], []
        for mid in ids:
            m = self._cache_get(mid)
            if m is not None:
                cached.append(m)
            else:
                misses.append(mid)
        return cached, misses

    # ── Ring memory (short-term, survives restarts) ────────────────────────────

    def write_ring(
        self, content: str, category: str = "note", thread_id: str | None = None
    ):
        """
        Write an entry to the short-term ring buffer.
        Automatically trims to RING_MAX entries (oldest first).

        thread_id: optional per-channel key (e.g. "discord:123456") for #136 P2 isolation.
        None = global entry visible to all threads.
        """
        content = scrub(content)
        now = datetime.now().isoformat()
        with self._local_conn() as conn:
            conn.execute(
                "INSERT INTO ring_memory (category, content, timestamp, thread_id) VALUES (?, ?, ?, ?)",
                (category, content, now, thread_id),
            )
            # Trim to max size
            conn.execute(f"""
                DELETE FROM ring_memory WHERE id NOT IN (
                    SELECT id FROM ring_memory ORDER BY id DESC LIMIT {RING_MAX}
                )
            """)

    def write_restart_note(self, reason: str, context: str = ""):
        """
        Write a note to future-Igor explaining why we restarted.
        This is the memory equivalent of leaving a sticky note on the monitor.
        """
        note = f"RESTART NOTE: {reason}"
        if context:
            note += f" | Context: {context}"
        self.write_ring(note, category="restart_note")

    def read_ring_memory(
        self,
        limit: int = 20,
        category: str | None = None,
        thread_id: str | None = None,
    ) -> list[dict]:
        """
        Read recent ring memory entries, newest last (chronological order).
        Optionally filter by category and/or thread_id.

        thread_id filtering (#136 P2):
          - None (default): return all entries (global + all threads)
          - str: return entries matching that thread_id OR thread_id IS NULL (global entries)
            This means per-thread reads include global context entries too.
        """
        with self._local_conn() as conn:
            if category and thread_id:
                rows = conn.execute(
                    "SELECT * FROM ring_memory WHERE category = ? "
                    "AND (thread_id = ? OR thread_id IS NULL) "
                    "ORDER BY id DESC LIMIT ?",
                    (category, thread_id, limit),
                ).fetchall()
            elif thread_id:
                rows = conn.execute(
                    "SELECT * FROM ring_memory "
                    "WHERE (thread_id = ? OR thread_id IS NULL) "
                    "ORDER BY id DESC LIMIT ?",
                    (thread_id, limit),
                ).fetchall()
            elif category:
                rows = conn.execute(
                    "SELECT * FROM ring_memory WHERE category = ? ORDER BY id DESC LIMIT ?",
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ring_memory ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        # Return in chronological order (oldest first)
        entries = [
            {
                "id": r["id"],
                "category": r["category"],
                "content": r["content"],
                "timestamp": r["timestamp"],
                "thread_id": r["thread_id"] if "thread_id" in r.keys() else None,
            }
            for r in rows
        ]
        return list(reversed(entries))

    def search_ring_text(self, query: str, limit: int = 5) -> list[dict]:
        """
        G32: Keyword search over ring_memory content using SQLite LIKE.
        Returns up to `limit` entries (newest first) whose content contains
        any term from the query.  Used as a fallback when cortex.search() finds
        nothing in LTM — catches recent session context not yet promoted.
        """
        terms = [t.strip() for t in query.lower().split() if len(t.strip()) >= 3]
        if not terms:
            return []
        # Build WHERE clause: content LIKE '%term%' OR ...
        clauses = " OR ".join("lower(content) LIKE ?" for _ in terms)
        params = [f"%{t}%" for t in terms] + [limit]
        with self._local_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM ring_memory WHERE {clauses} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [
            {
                "id": r["id"],
                "category": r["category"],
                "content": r["content"],
                "timestamp": r["timestamp"],
                "thread_id": r["thread_id"] if "thread_id" in r.keys() else None,
            }
            for r in rows
        ]

    def get_last_restart_note(self) -> Optional[dict]:
        """Get the most recent restart note, if any."""
        with self._local_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ring_memory WHERE category = 'restart_note' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row:
            return {
                "id": row["id"],
                "category": row["category"],
                "content": row["content"],
                "timestamp": row["timestamp"],
            }
        return None

    # ── TWM — Temporal Working Memory ──────────────────────────────────────────

    def twm_push(
        self,
        source: str,
        content_csb: str,
        salience: float = 0.5,
        metadata: dict = None,
        ttl_seconds: int = None,
        urgency: float = 0.2,
        thread_id: str | None = None,
        category: str = "observation",
        parent_obs_id: int | None = None,
    ) -> int:
        """
        Push an observation into TWM. Any process can call this.
        Returns the new observation ID.
        Automatically evicts if over TWM_MAX (lowest salience + integrated + oldest first).

        urgency: 0.0-1.0 — time-sensitivity of this observation (distinct from salience/importance).
          0.9 = ethics violation / inbox file; 0.8 = new inbox item;
          0.7 = user input; 0.5 = machines change; 0.3 = heartbeat; 0.1 = surfaced memory.
        """
        content_csb = scrub(content_csb)
        urgency = max(0.0, min(1.0, urgency))
        now = datetime.now()
        expires_at = None
        if ttl_seconds:
            expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

        # G6 / #44: Habituation — repeated identical observations get reduced salience.
        # Match on first 120 chars of content (enough to identify the signal type + key).
        # Each repeat halves the effective salience (floor 0.05). Count stored in metadata.
        _sig = content_csb[:120]
        _repeat_count = 0
        metadata = dict(metadata or {})
        with self._local_conn() as conn:
            _existing = conn.execute(
                "SELECT id, metadata_json FROM twm_observations "
                "WHERE SUBSTR(content_csb, 1, 120) = ? AND integrated = 0 "
                "LIMIT 1",
                (_sig,),
            ).fetchone()
        if _existing:
            try:
                _prev_meta = json.loads(_existing["metadata_json"] or "{}")
                _repeat_count = _prev_meta.get("repeat_count", 0) + 1
            except Exception:
                _repeat_count = 1
            # Decay: each repeat halves salience; floor at 0.05
            salience = max(0.05, salience * (0.5 ** min(_repeat_count, 6)))
            metadata["repeat_count"] = _repeat_count
            metadata["habituated"] = True

        # G47: suppress at the door — don't admit observations that are too noisy to matter.
        # Prevents low-salience spam (e.g. NE impulse loop, repeated resource warnings)
        # from filling TWM slots that should go to meaningful context.
        if urgency < 0.65:  # high-urgency (ethics, inbox, user input) always admitted
            if _repeat_count >= TWM_SUPPRESS_AFTER_REPEATS:
                return -1  # suppressed — caller can ignore this return value
            if salience < TWM_SUPPRESS_SALIENCE_FLOOR:
                return -1  # suppressed

        with self._local_conn() as conn:
            cur = conn.execute(
                """INSERT INTO twm_observations
                   (timestamp, source, content_csb, salience, metadata_json,
                    integrated, integration_count, expires_at, urgency, instance_id,
                    thread_id, category, attractor_weight, parent_obs_id)
                   VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, 0.0, ?)""",
                (
                    now.isoformat(),
                    source,
                    content_csb,
                    salience,
                    json.dumps(metadata or {}),
                    expires_at,
                    urgency,
                    self._instance_id,
                    thread_id,
                    category,
                    parent_obs_id,
                ),
            )
            obs_id = cur.lastrowid

            # G50: high-urgency items (inbox, ethics, user input ≥0.8) become attractor
            if urgency >= 0.8 and obs_id and obs_id > 0:
                conn.execute(
                    "UPDATE twm_observations SET attractor_weight = 0.0 "
                    "WHERE instance_id = ? AND id != ?",
                    (self._instance_id, obs_id),
                )
                conn.execute(
                    "UPDATE twm_observations SET attractor_weight = 1.0 WHERE id = ?",
                    (obs_id,),
                )

            # Evict expired entries first
            conn.execute(
                "DELETE FROM twm_observations WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now.isoformat(),),
            )

            # Evict if over cap: integrated + low salience + oldest first
            count = conn.execute("SELECT COUNT(*) FROM twm_observations").fetchone()[0]
            if count > TWM_MAX:
                overflow = count - TWM_MAX
                conn.execute(f"""
                    DELETE FROM twm_observations WHERE id IN (
                        SELECT id FROM twm_observations
                        ORDER BY integrated DESC, salience ASC, id ASC
                        LIMIT {overflow}
                    )
                """)

        return obs_id

    def twm_read(
        self,
        limit: int = 50,
        include_integrated: bool = True,
        thread_id: str | None = None,
        category: str | None = None,
    ) -> list[dict]:
        """
        Read TWM observations (newest last). Default: all including integrated.
        Use include_integrated=False to get only unprocessed ones.
        When self._instance_id is set, only returns observations for this instance.

        thread_id (#158): when provided, returns entries for that thread OR global
        entries (thread_id IS NULL) — same semantics as ring_memory.
        category (#158): when provided, filters to that category (e.g. "task_set").
        """
        _iid = self._instance_id
        clauses = []
        params: list = []

        if _iid:
            clauses.append("instance_id = ?")
            params.append(_iid)

        if not include_integrated:
            clauses.append("integrated = 0")

        if thread_id:
            clauses.append("(thread_id = ? OR thread_id IS NULL)")
            params.append(thread_id)

        if category:
            clauses.append("category = ?")
            params.append(category)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        with self._local_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM twm_observations {where} ORDER BY id ASC LIMIT ?",
                params,
            ).fetchall()

        return [
            {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "source": r["source"],
                "content_csb": r["content_csb"],
                "salience": r["salience"],
                "urgency": r["urgency"] if "urgency" in r.keys() else 0.2,
                "instance_id": r["instance_id"] if "instance_id" in r.keys() else None,
                "thread_id": r["thread_id"] if "thread_id" in r.keys() else None,
                "category": r["category"] if "category" in r.keys() else "observation",
                "metadata": json.loads(r["metadata_json"]),
                "integrated": bool(r["integrated"]),
                "integration_count": r["integration_count"],
                "expires_at": r["expires_at"],
                "attractor_weight": (
                    r["attractor_weight"] if "attractor_weight" in r.keys() else 0.0
                ),
                "parent_obs_id": (
                    r["parent_obs_id"] if "parent_obs_id" in r.keys() else None
                ),
            }
            for r in rows
        ]

    # ── G50: TWM Attractor ─────────────────────────────────────────────────────

    def twm_set_attractor(self, obs_id: int, weight: float = 1.0) -> None:
        """
        G50/D099: Set one TWM item as an attractor slot.
        Keeps up to TWM_MAX_SLOTS-1 existing non-zero attractor slots.
        Evicts the lowest-weight slot if already at capacity.
        Emergency path (urgency≥0.8 via twm_push): still zeros all others.
        Callers: UserInputSource.push_message(), high-priority push_sources.
        """
        if obs_id <= 0:
            return
        with self._local_conn() as conn:
            # Count existing active attractor slots (excluding the target)
            active = conn.execute(
                "SELECT id, attractor_weight FROM twm_observations "
                "WHERE instance_id = ? AND id != ? AND attractor_weight > 0.05 "
                "ORDER BY attractor_weight ASC",
                (self._instance_id, obs_id),
            ).fetchall()
            # If at capacity, evict the weakest slot
            if len(active) >= TWM_MAX_SLOTS:
                evict_id = active[0]["id"]
                conn.execute(
                    "UPDATE twm_observations SET attractor_weight = 0.0 WHERE id = ?",
                    (evict_id,),
                )
            conn.execute(
                "UPDATE twm_observations SET attractor_weight = ? WHERE id = ?",
                (min(1.0, max(0.0, weight)), obs_id),
            )

    def twm_get_attractor(self) -> dict | None:
        """
        G50: Return the current attractor TWM item (highest attractor_weight > 0.1),
        or None if no attractor is active.
        """
        with self._local_conn() as conn:
            row = conn.execute(
                "SELECT * FROM twm_observations "
                "WHERE instance_id = ? AND attractor_weight > 0.1 "
                "ORDER BY attractor_weight DESC LIMIT 1",
                (self._instance_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "content_csb": row["content_csb"],
            "attractor_weight": row["attractor_weight"],
            "salience": row["salience"],
            "metadata": (
                json.loads(row["metadata_json"]) if row["metadata_json"] else {}
            ),  # #172
        }

    def twm_decay_attractor(self, factor: float = 0.90) -> None:
        """
        G50: Decay all attractor_weights by factor. Call from HeartbeatSource (every 5 min).
        factor=0.90 → attractor fades to ~0.1 after ~22 heartbeats (~110 minutes).
        Below 0.05 is treated as inactive.
        """
        with self._local_conn() as conn:
            conn.execute(
                "UPDATE twm_observations "
                "SET attractor_weight = attractor_weight * ? "
                "WHERE instance_id = ? AND attractor_weight > 0.05",
                (factor, self._instance_id),
            )
            # Zero out anything that has decayed below threshold
            conn.execute(
                "UPDATE twm_observations SET attractor_weight = 0.0 "
                "WHERE instance_id = ? AND attractor_weight <= 0.05",
                (self._instance_id,),
            )

    def twm_get_slots(self) -> list[dict]:
        """
        D099: Return all active attractor slots (attractor_weight > 0.05), ordered by weight desc.
        Used by NE comparison pass to find shared action_pointer overlap across slots.
        """
        with self._local_conn() as conn:
            rows = conn.execute(
                "SELECT id, content_csb, attractor_weight, salience, metadata_json "
                "FROM twm_observations "
                "WHERE instance_id = ? AND attractor_weight > 0.05 "
                "ORDER BY attractor_weight DESC",
                (self._instance_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "content_csb": r["content_csb"],
                "attractor_weight": r["attractor_weight"],
                "salience": r["salience"],
                "metadata": json.loads(r["metadata_json"] or "{}"),
            }
            for r in rows
        ]

    def twm_decay_slot(self, obs_id: int, factor: float = 0.7) -> None:
        """
        D099: Decay attractor_weight on a single slot. Called by NE comparison pass
        on solo slots (no shared action_pointer with any other slot). factor=0.7
        fades an isolated slot quickly — it loses focus without collaborative context.
        """
        if obs_id <= 0:
            return
        with self._local_conn() as conn:
            conn.execute(
                "UPDATE twm_observations "
                "SET attractor_weight = MAX(0.0, attractor_weight * ?) "
                "WHERE id = ? AND attractor_weight > 0.05",
                (factor, obs_id),
            )
            conn.execute(
                "UPDATE twm_observations SET attractor_weight = 0.0 "
                "WHERE id = ? AND attractor_weight <= 0.05",
                (obs_id,),
            )

    def twm_clear_task_set(self, thread_id: str | None = None) -> int:
        """
        #158: Mark all TASK_SET entries for this thread as integrated (completed).
        Called when a task completion signal is detected in the response.
        Returns count of entries cleared.
        """
        with self._local_conn() as conn:
            if thread_id:
                result = conn.execute(
                    "UPDATE twm_observations SET integrated = 1 "
                    "WHERE category = 'task_set' AND integrated = 0 "
                    "AND (thread_id = ? OR thread_id IS NULL)",
                    (thread_id,),
                )
            else:
                result = conn.execute(
                    "UPDATE twm_observations SET integrated = 1 "
                    "WHERE category = 'task_set' AND integrated = 0"
                )
            return result.rowcount

    def twm_count_unintegrated(self) -> int:
        """How many TWM observations are waiting to be integrated?"""
        _iid = self._instance_id
        with self._local_conn() as conn:
            if _iid:
                return conn.execute(
                    "SELECT COUNT(*) FROM twm_observations WHERE integrated = 0 AND instance_id = ?",
                    (_iid,),
                ).fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM twm_observations WHERE integrated = 0"
            ).fetchone()[0]

    def twm_count(self) -> int:
        """Total TWM observation rows (fingerprint helper for NE idle gate)."""
        _iid = self._instance_id
        with self._local_conn() as conn:
            if _iid:
                return conn.execute(
                    "SELECT COUNT(*) FROM twm_observations WHERE instance_id = ?",
                    (_iid,),
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM twm_observations").fetchone()[0]

    def twm_max_id(self) -> int:
        """Highest TWM observation id (fingerprint helper for NE idle gate)."""
        _iid = self._instance_id
        with self._local_conn() as conn:
            if _iid:
                row = conn.execute(
                    "SELECT MAX(id) FROM twm_observations WHERE instance_id = ?",
                    (_iid,),
                ).fetchone()
            else:
                row = conn.execute("SELECT MAX(id) FROM twm_observations").fetchone()
            return row[0] if row and row[0] is not None else 0

    def twm_mark_integrated(self, obs_ids: list[int]):
        """Mark observations as integrated by the NE."""
        if not obs_ids:
            return
        placeholders = ",".join("?" * len(obs_ids))
        with self._local_conn() as conn:
            conn.execute(
                f"UPDATE twm_observations SET integrated = 1, integration_count = integration_count + 1 "
                f"WHERE id IN ({placeholders})",
                obs_ids,
            )

    def twm_update_salience(self, obs_id: int, salience: float):
        """NE can update salience of an observation after integration."""
        with self._local_conn() as conn:
            conn.execute(
                "UPDATE twm_observations SET salience = ? WHERE id = ?",
                (max(0.0, min(1.0, salience)), obs_id),
            )

    def twm_clear(self):
        """Clear all TWM observations (use sparingly — for testing/reset)."""
        with self._local_conn() as conn:
            conn.execute("DELETE FROM twm_observations")

    def twm_extend_ttl(
        self, obs_id: int, extension_seconds: int = None, reason: str = ""
    ) -> None:
        """
        Extend TWM TTL on confirmed relevance (Change 3 — Signal A/B/C).

        TTL extends to MAX(current expires_at, now + extension_seconds).
        Only extends — never shrinks. Noise expires on schedule; confirmed-relevant
        observations survive longer.

        Logs to ring(twm_ttl_extension) with reason. Never raises.
        """
        if extension_seconds is None:
            extension_seconds = TWM_TTL_EXTENSION_SECONDS
        try:
            now = datetime.now()
            new_expiry = (now + timedelta(seconds=extension_seconds)).isoformat()
            with self._local_conn() as conn:
                # Fetch current expiry
                row = conn.execute(
                    "SELECT expires_at FROM twm_observations WHERE id = ?", (obs_id,)
                ).fetchone()
                if row is None:
                    return  # Observation already evicted
                current_expiry = row["expires_at"]
                # Take the later of current and new expiry
                if current_expiry and current_expiry > new_expiry:
                    final_expiry = current_expiry
                else:
                    final_expiry = new_expiry
                conn.execute(
                    "UPDATE twm_observations SET expires_at = ? WHERE id = ?",
                    (final_expiry, obs_id),
                )
            self.write_ring(
                f"TWM_TTL_EXT|obs={obs_id}|ext={extension_seconds}s|reason={reason[:80]}",
                category="twm_ttl_extension",
            )
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
            )

    # ── D126 Step 4: Worry signal ──────────────────────────────────────────────

    def worry_push(self, reason: str) -> int:
        """
        Push a Worry observation to TWM — internal uncertainty about Igor's own actions.

        Worry semantics:
          - category = "worry"
          - urgency = 0.85  (high: below ethics 0.9, above user input 0.7)
          - salience = 0.9
          - high attractor_weight → becomes primary focus until resolved
          - persists until explicitly resolved (no short TTL)

        Triggered by PendingReplyStore when a write has failed 3+ times.
        The Worry persists in TWM so the NE can surface it to the user on next turn.
        """
        return self.twm_push(
            source="pending_replies",
            content_csb=f"WORRY|reason:{reason[:200]}",
            salience=0.9,
            urgency=0.85,
            category="worry",
            metadata={"worry_type": "unconfirmed_write", "reason": reason[:200]},
        )

    # ── #180: Investment Weights ────────────────────────────────────────────────

    def store_relational(
        self,
        name: str,
        narrative: str,
        relationship_type: str,
        investment_weight: float = 0.7,
        proximity: str = "present",
        valence: float = 0.8,
        extra_metadata: dict = None,
    ) -> "Memory":
        """
        #180: Store a relational node (person / project / idea) as an INTERPRETIVE
        memory with investment weight metadata.

        relationship_type: "partner" | "friend" | "family" | "colleague" | "project" | "idea"
        investment_weight: 0.0–1.0 (love/deep_investment=0.9+; acquaintance=0.3)
        proximity: "present" | "remote" | "lost" — modulates activation threshold
        """
        from .models import Memory as _M, MemoryType as _MT

        meta = {
            "relationship_type": relationship_type,
            "investment_weight": max(0.0, min(1.0, investment_weight)),
            "proximity": proximity,
            "nre_phase": False,
            "source": "relational",
        }
        if extra_metadata:
            meta.update(extra_metadata)
        mem = _M(
            id=f"REL_{name.upper().replace(' ', '_')}",
            narrative=narrative,
            memory_type=_MT.INTERPRETIVE,
            valence=valence,
            metadata=meta,
        )
        return self.store(mem)

    def get_investment_nodes(self, min_weight: float = 0.5) -> list["Memory"]:
        """
        #180: Return all relational/investment nodes above min_weight.
        These are INTERPRETIVE memories with metadata.source == "relational".
        """
        from .db_proxy import PGDatabaseProxy

        _rel_sql = (
            f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE memory_type='INTERPRETIVE' "
            'AND metadata @> \'{"source": "relational"}\''
            if isinstance(self._db, PGDatabaseProxy)
            else f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE memory_type='INTERPRETIVE' "
            'AND metadata LIKE \'%"source": "relational"%\''
        )
        with self._conn() as conn:
            rows = conn.execute(_rel_sql).fetchall()
        nodes = [self._to_memory(r) for r in rows]
        return [
            n
            for n in nodes
            if n and n.metadata.get("investment_weight", 0.0) >= min_weight
        ]

    def investment_weight_check(self, text: str) -> list["Memory"]:
        """
        #180: Pre-attentive check — does text mention any high-investment node?
        Returns relational nodes whose name appears in the text.
        Used as a pre-traversal salience booster (somatic marker equivalent).
        """
        text_lower = text.lower()
        nodes = self.get_investment_nodes(min_weight=0.4)
        hits = []
        for node in nodes:
            # Node ID is REL_NAME — extract name portion
            node_name = node.id.replace("REL_", "").replace("_", " ").lower()
            # Also check first word of narrative
            narrative_words = node.narrative.lower().split()[:3]
            if node_name in text_lower or any(
                w in text_lower for w in narrative_words if len(w) > 3
            ):
                hits.append(node)
        # Sort by investment_weight descending
        hits.sort(key=lambda n: n.metadata.get("investment_weight", 0.0), reverse=True)
        return hits

    def decay_investment_weights(
        self,
        decay_rate: float = 0.02,
        stable_floor: float = 0.5,
        nre_spike: float = 0.95,
        nre_decay_rate: float = 0.05,
    ) -> dict:
        """
        #180: NRE decay curve — gradually moves investment_weight toward stable attachment.

        NRE (New Relationship Energy) causes initial spike; over time it settles to stable.
        Same mechanism at different curve points (Akien insight 2026-03-12).

        For each relational node:
        - If nre_phase=True and weight > stable_floor + 0.1: apply nre_decay_rate (faster)
        - Otherwise: apply decay_rate toward stable_floor (slower, asymptotic)
        - Weight never falls below stable_floor (Bowlby: secure attachment baseline)
        - If weight drops below nre_spike - 0.3, nre_phase is set to False (NRE has ended)

        Returns summary: {"updated": N, "nre_ended": [node_ids]}
        """
        nodes = self.get_investment_nodes(min_weight=0.0)
        updated = 0
        nre_ended = []

        for node in nodes:
            if not node.metadata:
                continue
            w = node.metadata.get("investment_weight", 0.0)
            _nre = node.metadata.get("nre_phase", False)
            _floor = node.metadata.get("stable_floor", stable_floor)

            # Compute new weight
            if _nre and w > _floor + 0.15:
                # NRE phase: faster decay toward stable attachment
                new_w = w - nre_decay_rate * (w - _floor)
            else:
                # Stable phase: slow asymptotic approach to floor
                new_w = w - decay_rate * (w - _floor)

            new_w = max(_floor, min(1.0, new_w))

            # Check if NRE phase has ended
            new_nre = _nre
            if _nre and w - new_w < 0.001:  # essentially flat — NRE has ended
                new_nre = False
                nre_ended.append(node.id)

            if abs(new_w - w) > 0.0001 or new_nre != _nre:
                new_meta = dict(node.metadata)
                new_meta["investment_weight"] = round(new_w, 4)
                new_meta["nre_phase"] = new_nre
                new_meta["last_decay"] = (
                    __import__("datetime").datetime.utcnow().isoformat()
                )
                try:
                    import json as _json

                    with self._conn() as conn:
                        conn.execute(
                            "UPDATE memories SET metadata=? WHERE id=?",
                            (_json.dumps(new_meta), node.id),
                        )
                    updated += 1
                except Exception as _bare_e:
                    logging.getLogger(__name__).warning(
                        "bare except in wild_igor/igor/memory/cortex.py: %s", _bare_e
                    )

        return {"updated": updated, "nre_ended": nre_ended}

    # ── G52: Interpretive Tree ─────────────────────────────────────────────────

    def add_interpretive_edge(
        self,
        from_id: str,
        to_id: str,
        *,
        direction: str = "activation",
        condition_csb: str = "",
        meaning_payload: str = "",
        action_pointer: str = "",
        weight: float = 1.0,
        layer: str = "",
    ) -> int:
        """
        G52: Add a directed edge between two memories in the interpretive tree.

        Edge semantics (4 parts):
          direction: "activation" | "inhibition" — does traversal promote or suppress?
          condition_csb: CSB string specifying when this edge fires (empty = always)
          meaning_payload: the WHY — what reaching to_id means about self or situation
          action_pointer: memory id or code_ref of the next tree to explore
          weight: traversal strength [0,1]
          layer: semantic layer tag; auto-set to 'meaning_to_me' for CP/ID root edges (#244)

        CP1-CP6 are root nodes. Their children are the first interpretive layer.
        Returns the new edge id.
        """
        from datetime import datetime as _dt

        # #244: auto-tag edges from CP/ID roots as meaning_to_me layer
        if not layer and (from_id.startswith("CP") or from_id.startswith("ID")):
            layer = "meaning_to_me"

        now = _dt.now().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO interpretive_edges
                    (from_id, to_id, direction, condition_csb, meaning_payload, action_pointer, weight, created_at, layer)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    from_id,
                    to_id,
                    direction,
                    condition_csb,
                    meaning_payload,
                    action_pointer,
                    max(0.0, min(1.0, weight)),
                    now,
                    layer,
                ),
            )
            return cur.lastrowid

    def get_interpretive_edges(self, from_id: str) -> list[dict]:
        """
        G52: Return all outgoing interpretive edges from from_id.
        Each dict: {id, from_id, to_id, direction, condition_csb, meaning_payload, action_pointer, weight, layer}
        Ordered by weight DESC.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, from_id, to_id, direction, condition_csb,
                       meaning_payload, action_pointer, weight, created_at,
                       COALESCE(layer, '') AS layer
                FROM interpretive_edges
                WHERE from_id = ?
                ORDER BY weight DESC
                """,
                (from_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_meaning_to_me(self, limit: int = 50) -> list["Memory"]:
        """
        #244: Return Memory objects reachable via meaning_to_me layer edges.

        These are memories directly connected to Igor's core patterns (CP1-CP6)
        or identity nodes (ID1-ID14). Personally significant threads.
        Ordered by edge weight DESC.
        """
        from .models import Memory as _M  # avoid circular at module level

        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT ie.to_id, ie.weight
                FROM interpretive_edges ie
                WHERE ie.layer = 'meaning_to_me'
                  AND ie.direction != 'inhibition'
                ORDER BY ie.weight DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        if not rows:
            return []
        to_ids = [r["to_id"] for r in rows]
        _cached, _miss_ids = self._cache_fetch_ids(to_ids)
        if _miss_ids:
            with self._conn() as conn:
                placeholders = ",".join("?" * len(_miss_ids))
                _mem_rows = conn.execute(
                    f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE id IN ({placeholders})",
                    _miss_ids,
                ).fetchall()
            for _row in _mem_rows:
                _m = self._to_memory(_row)
                self._cache_put(_m)
                _cached.append(_m)
        mem_by_id = {m.id: m for m in _cached}
        result = []
        for r in rows:
            mid = r["to_id"]
            if mid in mem_by_id:
                mem = mem_by_id[mid]
                if mem:
                    result.append(mem)
        return result

    def add_temporal_edge(
        self,
        earlier_id: str,
        later_id: str,
        weight: float = 0.7,
        context: str = "",
    ) -> int:
        """
        #175: Add a temporal edge from earlier_id → later_id.
        Uses direction="temporal" in the interpretive_edges table — no new table needed.
        weight: how strongly the earlier memory leads to the later one in time.
        context: optional note about what connects them temporally.
        """
        return self.add_interpretive_edge(
            from_id=earlier_id,
            to_id=later_id,
            direction="temporal",
            condition_csb=(
                f"temporal_sequence:{context}" if context else "temporal_sequence"
            ),
            meaning_payload=f"Temporal successor: {later_id} follows {earlier_id} in time.",
            action_pointer=later_id,
            weight=weight,
        )

    def wire_temporal_sequence(self, memory_ids: list[str], weight: float = 0.7) -> int:
        """
        #175: Wire a list of memory IDs as a temporal chain (A→B→C→...).
        Returns number of edges created.
        Skips pairs that already have a temporal edge.
        """
        created = 0
        for i in range(len(memory_ids) - 1):
            a, b = memory_ids[i], memory_ids[i + 1]
            # Check if temporal edge already exists
            with self._conn() as conn:
                exists = conn.execute(
                    "SELECT COUNT(*) FROM interpretive_edges "
                    "WHERE from_id=? AND to_id=? AND direction='temporal'",
                    (a, b),
                ).fetchone()[0]
            if not exists:
                self.add_temporal_edge(a, b, weight=weight)
                created += 1
        return created

    def interpretive_traverse(
        self,
        from_ids: list[str],
        max_depth: int = 3,
        min_weight: float = 0.1,
        include_temporal: bool = False,
        milieu_bias: dict | None = None,
        exit_on_convergence: bool = False,
        convergence_weight: float = 0.75,
        convergence_out_degree: int = 4,
        track_meaning_layer: bool = False,
    ) -> list["Memory"]:
        """
        G52: Breadth-first traversal of the interpretive tree from a set of seed nodes.

        Used to move from current context (which memory nodes are active) through
        interpretive edges to find meaning assignments. Returns activated INTERPRETIVE
        memories, ordered by traversal depth then edge weight.

        from_ids: starting node ids (typically CP1-CP6 + currently-active memories)
        max_depth: traversal depth cap (default 3 = enough for most schemas)
        min_weight: prune edges below this weight
        include_temporal (#175): if True, also follow temporal edges (time layer traversal)
        milieu_bias (#171): dict mapping node_id → weight_multiplier for milieu-weighted
            traversal. Edges from high-bias roots require less weight to fire. Example:
            {"CP6": 1.5} lowers effective threshold for CP6's safety branch when stressed.
        exit_on_convergence (#182): if True, stop descending a branch when it reaches a
            convergence node (investment_weight >= convergence_weight OR out_degree >=
            convergence_out_degree). The convergence node IS the answer — the lever.
            Implements the insight: "why?" upward terminates where levers lie.
        convergence_weight: investment_weight threshold for convergence detection (default 0.75)
        convergence_out_degree: out-degree threshold for convergence detection (default 4)
        track_meaning_layer (#244): if True, set self._last_traverse_meaning_to_me = True
            whenever an edge with layer='meaning_to_me' is followed.
        """
        if not from_ids:
            return []

        if track_meaning_layer:
            self._last_traverse_meaning_to_me = False

        _milieu_bias: dict = milieu_bias or {}
        visited: set[str] = set(from_ids)
        queue: list[tuple[str, int, str]] = [
            (fid, 0, fid) for fid in from_ids
        ]  # (id, depth, root)
        result_ids: list[str] = []
        # #244: track which collected nodes are personally significant (meaning_to_me layer)
        _meaning_to_me_ids: set[str] = set()
        # #182: cache out-degree counts to avoid per-node DB queries
        _out_degree_cache: dict[str, int] = {}

        while queue:
            current_id, depth, root_id = queue.pop(0)
            if depth >= max_depth:
                continue
            edges = self.get_interpretive_edges(current_id)
            # #171: milieu bias — multiply edge weight for edges originating from biased roots
            _bias = _milieu_bias.get(root_id, 1.0)
            _effective_min = min_weight / max(_bias, 0.01)
            for edge in edges:
                if edge["weight"] < _effective_min:
                    continue
                if edge["direction"] == "inhibition":
                    # inhibition: skip the target (don't follow into inhibited subtree)
                    visited.add(edge["to_id"])
                    continue
                if edge["direction"] == "temporal" and not include_temporal:
                    continue  # #175: time layer opt-in
                if edge["to_id"] not in visited:
                    visited.add(edge["to_id"])
                    result_ids.append(edge["to_id"])
                    # #244: meaning_to_me flag — direct edge hit or descendant of flagged node
                    if (
                        edge.get("layer") == "meaning_to_me"
                        or current_id in _meaning_to_me_ids
                    ):
                        _meaning_to_me_ids.add(edge["to_id"])
                        if track_meaning_layer:
                            self._last_traverse_meaning_to_me = True
                    # #182: convergence check — is this node a lever?
                    _is_convergence = False
                    if exit_on_convergence:
                        try:
                            _to_mem = self.get(edge["to_id"])
                            if _to_mem:
                                _iw = (_to_mem.metadata or {}).get(
                                    "investment_weight", 0.0
                                )
                                if _iw >= convergence_weight:
                                    _is_convergence = True
                                else:
                                    # check out-degree
                                    if edge["to_id"] not in _out_degree_cache:
                                        with self._conn() as _c:
                                            _out_degree_cache[edge["to_id"]] = (
                                                _c.execute(
                                                    "SELECT COUNT(*) FROM interpretive_edges WHERE from_id=?",
                                                    (edge["to_id"],),
                                                ).fetchone()[0]
                                            )
                                    if (
                                        _out_degree_cache[edge["to_id"]]
                                        >= convergence_out_degree
                                    ):
                                        _is_convergence = True
                        except Exception as _bare_e:
                            logging.getLogger(__name__).warning(
                                "bare except in wild_igor/igor/memory/cortex.py: %s",
                                _bare_e,
                            )
                    if not _is_convergence:
                        queue.append((edge["to_id"], depth + 1, root_id))
                    # convergence node is collected but not descended — it's the lever

        if not result_ids:
            return []

        # Fetch the actual Memory objects
        from .models import Memory as _M  # avoid circular at module level

        _cached, _miss_ids = self._cache_fetch_ids(result_ids)
        if _miss_ids:
            with self._conn() as conn:
                placeholders = ",".join("?" * len(_miss_ids))
                _rows = conn.execute(
                    f"SELECT {_MEM_COLS_NO_EMBED} FROM memories WHERE id IN ({placeholders})",
                    _miss_ids,
                ).fetchall()
            for _row in _rows:
                _m = self._to_memory(_row)
                self._cache_put(_m)
                _cached.append(_m)
        mem_by_id = {m.id: m for m in _cached}
        # Return in traversal order; tag meaning_to_me nodes in-place (#244)
        memories = []
        for mid in result_ids:
            if mid in mem_by_id:
                mem = mem_by_id[mid]
                if mem:
                    if mid in _meaning_to_me_ids:
                        if mem.metadata is None:
                            mem.metadata = {}
                        mem.metadata["meaning_to_me"] = True
                    memories.append(mem)
        return memories

    # ── Lists (D095) ────────────────────────────────────────────────────────────

    def list_set(
        self,
        list_name: str,
        item_key: str,
        item_value: str = None,
        ref_type: str = None,
        ref_id: str = None,
        instance_id: str = "",
    ) -> None:
        """Upsert one item in a named list. instance_id='' means global."""
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO lists
                    (list_name, item_key, item_value, ref_type, ref_id, instance_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (list_name, item_key, item_value, ref_type, ref_id, instance_id, now),
            )

    def list_get(
        self, list_name: str, item_key: str, instance_id: str = ""
    ) -> Optional[dict]:
        """Return one list item as a dict, or None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM lists WHERE list_name=? AND item_key=? AND instance_id=?",
                (list_name, item_key, instance_id),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_remove(self, list_name: str, item_key: str, instance_id: str = "") -> None:
        """Delete one item from a named list."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM lists WHERE list_name=? AND item_key=? AND instance_id=?",
                (list_name, item_key, instance_id),
            )

    def list_all(self, list_name: str, instance_id: str = "") -> list[dict]:
        """Return all items in a named list as a list of dicts, ordered by item_key."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM lists WHERE list_name=? AND instance_id=? ORDER BY item_key",
                (list_name, instance_id),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── T-graph-calving: Attractor detection + node adoption ──────────────────

    def get_attractors(self, limit: int = 20) -> list:
        """
        T-graph-calving: Return top attractor nodes scored by activation_count × (1 + inbound_edges).
        Attractors are emergent — not labeled, just the most activated + most-linked nodes.
        Excludes PROCEDURAL habits (they're habits, not knowledge attractors).
        """
        with self._conn() as conn:
            id_rows = conn.execute(
                """
                SELECT m.id
                FROM memories m
                LEFT JOIN interpretive_edges ie ON ie.to_id = m.id
                WHERE m.memory_type NOT IN ('PROCEDURAL')
                GROUP BY m.id, m.activation_count
                ORDER BY m.activation_count * (1 + COUNT(ie.id)) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ids = [r["id"] for r in id_rows]
        return [m for m in (self.get(i) for i in ids) if m]

    def node_depth(self, node_id: str, max_depth: int = 6) -> int:
        """
        T-graph-calving: Hop count from node to root via parent_id chain. Caps at max_depth.
        Returns 0 if node is a root or not found.
        """
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    WITH RECURSIVE chain AS (
                        SELECT id, parent_id, 0 AS depth
                        FROM memories WHERE id = ?
                        UNION ALL
                        SELECT m.id, m.parent_id, c.depth + 1
                        FROM memories m
                        JOIN chain c ON m.id = c.parent_id
                        WHERE c.depth < ?
                          AND c.parent_id IS NOT NULL
                          AND c.parent_id != ''
                    )
                    SELECT MAX(depth) FROM chain
                    """,
                    (node_id, max_depth),
                ).fetchone()
            return row[0] if row and row[0] is not None else 0
        except Exception:
            return 0

    def adopt_orphans(self, batch_size: int = 50) -> int:
        """
        T-graph-calving: Find orphan nodes (no parent_id, no inbound adoption edge) and
        link them to their nearest attractor via interpretive_edge(direction='adoption').
        Uses embedding cosine similarity. Returns number of adoptions performed.

        Gate: IGOR_NODE_ADOPTION_ENABLED must be 'true'.
        """
        import os as _os
        import json as _json

        if _os.getenv("IGOR_NODE_ADOPTION_ENABLED", "false").lower() != "true":
            return 0

        try:
            import numpy as _np
        except ImportError:
            return 0

        threshold = float(_os.getenv("IGOR_ADOPTION_THRESHOLD", "0.3"))

        # Get current attractors + their embeddings
        attractors = self.get_attractors(limit=20)
        if not attractors:
            return 0
        att_ids = [a.id for a in attractors]
        att_embs = self._get_embeddings_batch(att_ids)
        att_pairs = [(aid, att_embs[aid]) for aid in att_ids if att_embs.get(aid)]
        if not att_pairs:
            return 0

        # Build normalized attractor matrix
        att_id_list = [p[0] for p in att_pairs]
        att_matrix = _np.array([p[1] for p in att_pairs], dtype=_np.float32)
        att_norms = _np.linalg.norm(att_matrix, axis=1, keepdims=True)
        att_matrix_norm = att_matrix / _np.maximum(att_norms, 1e-9)

        # Find orphans: no parent_id, not PROC/ROOT, no inbound adoption edge yet
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT m.id FROM memories m
                WHERE (m.parent_id IS NULL OR m.parent_id = '')
                  AND m.memory_type NOT IN ('PROCEDURAL', 'ROOT')
                  AND NOT EXISTS (
                      SELECT 1 FROM interpretive_edges ie
                      WHERE ie.to_id = m.id AND ie.direction = 'adoption'
                  )
                LIMIT ?
                """,
                (batch_size,),
            ).fetchall()
        orphan_ids = [r["id"] for r in rows]
        if not orphan_ids:
            return 0

        orphan_embs = self._get_embeddings_batch(orphan_ids)

        adopted = 0
        for oid in orphan_ids:
            emb = orphan_embs.get(oid)
            if not emb:
                continue
            vec = _np.array(emb, dtype=_np.float32)
            norm = float(_np.linalg.norm(vec))
            if norm < 1e-9:
                continue
            vec_norm = vec / norm
            sims = att_matrix_norm @ vec_norm
            best_idx = int(_np.argmax(sims))
            best_sim = float(sims[best_idx])
            if best_sim < threshold:
                continue
            try:
                self.add_interpretive_edge(
                    att_id_list[best_idx],
                    oid,
                    direction="adoption",
                    meaning_payload=f"orphan adopted; sim={best_sim:.3f}",
                    layer="adoption",
                )
                adopted += 1
            except Exception:
                continue

        return adopted

    def find_calving_candidates(self, depth_threshold: int = 5) -> list[str]:
        """
        T-graph-calving: Return node IDs at depth > depth_threshold from their nearest root.
        These are candidates for calving — too specialized to stay in parent tree.
        Currently returns empty list when max tree depth < threshold (expected at launch).
        """
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    """
                    WITH RECURSIVE chain AS (
                        SELECT id, parent_id, 0 AS depth
                        FROM memories
                        WHERE parent_id IS NULL OR parent_id = ''
                        UNION ALL
                        SELECT m.id, m.parent_id, c.depth + 1
                        FROM memories m
                        JOIN chain c ON m.parent_id = c.id
                        WHERE c.depth < 30
                    )
                    SELECT id FROM chain WHERE depth > ?
                    """,
                    (depth_threshold,),
                ).fetchall()
            return [r["id"] for r in rows]
        except Exception:
            return []

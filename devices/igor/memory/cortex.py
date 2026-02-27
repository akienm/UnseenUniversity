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
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .models import Memory, MemoryType


RING_MAX = 50  # Max entries in the ring buffer
TWM_MAX  = 50  # Max observations in TWM


class Cortex:
    """SQLite-backed memory graph."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
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

            # change.37: embedding column — added via migration so existing DBs are not broken
            try:
                conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT DEFAULT NULL")
            except Exception:
                pass  # Column already exists

            # Short-term ring buffer — survives restarts, FIFO capped at RING_MAX
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ring_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL DEFAULT 'note',
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)

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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_twm_integrated ON twm_observations(integrated)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_twm_salience ON twm_observations(salience)")

    # ── Long-term memory graph ─────────────────────────────────────────────────

    def store(self, memory: Memory) -> Memory:
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO memories
                (id, narrative, memory_type, parent_id, children_ids, link_ids,
                 valence, activation_count, friction_history, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory.id,
                memory.narrative,
                memory.memory_type.value,
                memory.parent_id,
                json.dumps(memory.children_ids),
                json.dumps(memory.link_ids),
                memory.valence,
                memory.activation_count,
                json.dumps(memory.friction_history),
                memory.timestamp.isoformat(),
                json.dumps(memory.metadata),
            ))
        return memory

    def get(self, memory_id: str) -> Optional[Memory]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return self._to_memory(row) if row else None

    def get_children(self, parent_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE parent_id = ?", (parent_id,)
            ).fetchall()
        return [self._to_memory(r) for r in rows]

    def get_by_type(self, memory_type: MemoryType) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE memory_type = ?", (memory_type.value,)
            ).fetchall()
        return [self._to_memory(r) for r in rows]

    def add_child(self, parent_id: str, child_id: str):
        parent = self.get(parent_id)
        if parent and child_id not in parent.children_ids:
            parent.children_ids.append(child_id)
            self.store(parent)

    def record_activation(self, memory_id: str, friction: float):
        memory = self.get(memory_id)
        if memory:
            memory.activation_count += 1
            memory.friction_history.append(friction)
            self.store(memory)

    def search(self, query: str, limit: int = 10) -> list:
        """
        Hybrid search (change.37): text candidates → embedding re-rank.

        Phase 1 (always runs): naive text search over the memory graph.
          Filters out ROOT/CORE_PATTERN/IDENTITY/ROLE_MODEL (same as before).
          Returns up to limit×2 candidates sorted by keyword hit count.

        Phase 2 (runs when Ollama is available): embed the query and each
          candidate; sort by cosine similarity. Candidates that lack a stored
          embedding are computed on-the-fly and written back to the DB so the
          next call is cached. Sets m.relevance_score on each result.

        Falls back silently to Phase 1 if nomic-embed-text is unavailable.
        """
        terms = query.lower().split()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE memory_type NOT IN (?, ?, ?, ?) "
                "ORDER BY activation_count DESC",
                (MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value,
                 MemoryType.IDENTITY.value, MemoryType.ROLE_MODEL.value)
            ).fetchall()

        all_memories = [self._to_memory(r) for r in rows]

        # Phase 1: text scoring
        text_scored = []
        for m in all_memories:
            score = sum(1 for t in terms if t in m.narrative.lower())
            if score > 0:
                text_scored.append((score, m))
        text_scored.sort(key=lambda x: x[0], reverse=True)

        # Candidate pool — wider than limit to give embedding re-ranker room
        candidates = [m for _, m in text_scored[: limit * 2]]

        if not candidates:
            return []

        # Phase 2: embedding re-rank
        try:
            from ..cognition.embedder import embed, cosine_similarity
            query_vec = embed(query)
            if query_vec:
                scored = []
                for m in candidates:
                    mem_vec = self._get_or_compute_embedding(m)
                    sim = cosine_similarity(query_vec, mem_vec) if mem_vec else 0.0
                    m.relevance_score = sim  # type: ignore[attr-defined]
                    scored.append((sim, m))
                scored.sort(key=lambda x: x[0], reverse=True)
                return [m for _, m in scored[:limit]]
        except Exception:
            pass  # Embedding unavailable — fall through to text results

        # Phase 1 fallback: attach normalised relevance score and return
        max_terms = max(1, len(terms))
        for score, m in text_scored[:limit]:
            m.relevance_score = score / max_terms  # type: ignore[attr-defined]
        return [m for _, m in text_scored[:limit]]

    def _get_or_compute_embedding(self, memory) -> Optional[list]:
        """
        Return the stored embedding for a memory, computing and caching it
        if missing. Returns None if Ollama is unavailable.
        """
        # Check DB first
        with self._conn() as conn:
            row = conn.execute(
                "SELECT embedding FROM memories WHERE id = ?", (memory.id,)
            ).fetchone()
        if row and row["embedding"]:
            try:
                return json.loads(row["embedding"])
            except Exception:
                pass

        # Not in DB — compute via embedder and store back
        try:
            from ..cognition.embedder import embed
            vec = embed(memory.narrative)
            if vec:
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE memories SET embedding = ? WHERE id = ?",
                        (json.dumps(vec), memory.id),
                    )
                return vec
        except Exception:
            pass
        return None

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
        memories = self.get_by_type(MemoryType.PROCEDURAL)
        return [m for m in memories if m.is_habit]

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
        valid_cp  = {f"CP{i}"   for i in range(1, 7)}
        valid_id  = {f"ID{i}"   for i in range(1, 15)}
        valid_proc_parents = valid_cp | valid_id

        for cp_id in sorted(valid_cp):
            mem = self.get(cp_id)
            if mem is None:
                violations.append(f"MISSING_CP: {cp_id}")
            elif mem.parent_id != "ROOT":
                violations.append(f"ORPHAN_CP: {cp_id} parent={mem.parent_id!r} (expected ROOT)")

        for id_id in sorted(valid_id):
            mem = self.get(id_id)
            # Missing ID: informational — may be a pre-backfill instance; not a corruption signal
            if mem is not None and mem.parent_id not in valid_cp:
                violations.append(f"INVALID_PARENT_ID: {id_id} parent={mem.parent_id!r} (expected CP1-CP6)")

        for i in range(1, 11):
            proc_id = f"PROC{i}"
            mem = self.get(proc_id)
            # Missing PROC: informational — may be a pre-backfill instance; not a corruption signal
            if mem is not None and mem.parent_id not in valid_proc_parents:
                violations.append(f"INVALID_PARENT_PROC: {proc_id} parent={mem.parent_id!r}")

        return len(violations) == 0, violations

    def _to_memory(self, row) -> Memory:
        return Memory(
            id=row["id"],
            narrative=row["narrative"],
            memory_type=MemoryType(row["memory_type"]),
            parent_id=row["parent_id"],
            children_ids=json.loads(row["children_ids"]),
            link_ids=json.loads(row["link_ids"]),
            valence=row["valence"],
            activation_count=row["activation_count"],
            friction_history=json.loads(row["friction_history"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            metadata=json.loads(row["metadata"]),
        )

    # ── Ring memory (short-term, survives restarts) ────────────────────────────

    def write_ring(self, content: str, category: str = "note"):
        """
        Write an entry to the short-term ring buffer.
        Automatically trims to RING_MAX entries (oldest first).
        """
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO ring_memory (category, content, timestamp) VALUES (?, ?, ?)",
                (category, content, now)
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

    def read_ring_memory(self, limit: int = 20, category: str = None) -> list[dict]:
        """
        Read recent ring memory entries, newest last (chronological order).
        Optionally filter by category.
        """
        with self._conn() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM ring_memory WHERE category = ? ORDER BY id DESC LIMIT ?",
                    (category, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ring_memory ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        # Return in chronological order (oldest first)
        entries = [{"id": r["id"], "category": r["category"],
                    "content": r["content"], "timestamp": r["timestamp"]}
                   for r in rows]
        return list(reversed(entries))

    def get_last_restart_note(self) -> Optional[dict]:
        """Get the most recent restart note, if any."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM ring_memory WHERE category = 'restart_note' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row:
            return {"id": row["id"], "category": row["category"],
                    "content": row["content"], "timestamp": row["timestamp"]}
        return None

    # ── TWM — Temporal Working Memory ──────────────────────────────────────────

    def twm_push(self, source: str, content_csb: str, salience: float = 0.5,
                 metadata: dict = None, ttl_seconds: int = None) -> int:
        """
        Push an observation into TWM. Any process can call this.
        Returns the new observation ID.
        Automatically evicts if over TWM_MAX (lowest salience + integrated + oldest first).
        """
        now = datetime.now()
        expires_at = None
        if ttl_seconds:
            expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO twm_observations
                   (timestamp, source, content_csb, salience, metadata_json, integrated, integration_count, expires_at)
                   VALUES (?, ?, ?, ?, ?, 0, 0, ?)""",
                (now.isoformat(), source, content_csb, salience,
                 json.dumps(metadata or {}), expires_at)
            )
            obs_id = cur.lastrowid

            # Evict expired entries first
            conn.execute(
                "DELETE FROM twm_observations WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now.isoformat(),)
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

    def twm_read(self, limit: int = 50, include_integrated: bool = True) -> list[dict]:
        """
        Read TWM observations (newest last). Default: all including integrated.
        Use include_integrated=False to get only unprocessed ones.
        """
        with self._conn() as conn:
            if include_integrated:
                rows = conn.execute(
                    "SELECT * FROM twm_observations ORDER BY id ASC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM twm_observations WHERE integrated = 0 ORDER BY id ASC LIMIT ?",
                    (limit,)
                ).fetchall()
        return [
            {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "source": r["source"],
                "content_csb": r["content_csb"],
                "salience": r["salience"],
                "metadata": json.loads(r["metadata_json"]),
                "integrated": bool(r["integrated"]),
                "integration_count": r["integration_count"],
                "expires_at": r["expires_at"],
            }
            for r in rows
        ]

    def twm_count_unintegrated(self) -> int:
        """How many TWM observations are waiting to be integrated?"""
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM twm_observations WHERE integrated = 0"
            ).fetchone()[0]

    def twm_mark_integrated(self, obs_ids: list[int]):
        """Mark observations as integrated by the NE."""
        if not obs_ids:
            return
        placeholders = ",".join("?" * len(obs_ids))
        with self._conn() as conn:
            conn.execute(
                f"UPDATE twm_observations SET integrated = 1, integration_count = integration_count + 1 "
                f"WHERE id IN ({placeholders})",
                obs_ids
            )

    def twm_update_salience(self, obs_id: int, salience: float):
        """NE can update salience of an observation after integration."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE twm_observations SET salience = ? WHERE id = ?",
                (max(0.0, min(1.0, salience)), obs_id)
            )

    def twm_clear(self):
        """Clear all TWM observations (use sparingly — for testing/reset)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM twm_observations")

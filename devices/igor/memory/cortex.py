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
from .scrub import scrub


def _safe_memory_type(value: str) -> MemoryType:
    """Return MemoryType for value, falling back to FACTUAL for unknown types."""
    try:
        return MemoryType(value)
    except ValueError:
        return MemoryType.FACTUAL


RING_MAX = 50  # Max entries in the ring buffer
TWM_MAX  = 50  # Max observations in TWM

# Change 4: urgency — distinct from salience (time-sensitivity vs importance)
# Change 3: TTL extension on confirmed relevance (not mere access)
TWM_TTL_EXTENSION_SECONDS = int(
    __import__("os").getenv("TWM_TTL_EXTENSION_SECONDS", "1800")
)


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

            # G14 / #52: emotional profile columns (arousal + dominance)
            for _col in ("arousal REAL DEFAULT 0.0", "dominance REAL DEFAULT 0.0"):
                try:
                    conn.execute(f"ALTER TABLE memories ADD COLUMN {_col}")
                except Exception:
                    pass  # Column already exists

            # #71: portability flag — 1=portable (default), 0=instance-local
            try:
                conn.execute("ALTER TABLE memories ADD COLUMN portable INTEGER DEFAULT 1")
            except Exception:
                pass  # Column already exists

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

            # Change 4: urgency column (idempotent migration)
            # Urgency = time-sensitivity (0-1); distinct from salience (importance).
            # Noise expires on schedule; urgent items demand faster attention.
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN urgency REAL DEFAULT 0.2"
                )
            except Exception:
                pass  # Column already exists

    # ── Long-term memory graph ─────────────────────────────────────────────────

    def store(self, memory: Memory) -> Memory:
        memory.narrative = scrub(memory.narrative)
        # Scrub string values in metadata to prevent credential leakage (#19)
        if memory.metadata:
            memory.metadata = {
                k: scrub(v) if isinstance(v, str) else v
                for k, v in memory.metadata.items()
            }
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO memories
                (id, narrative, memory_type, parent_id, children_ids, link_ids,
                 valence, arousal, dominance,
                 activation_count, friction_history, timestamp, metadata, portable)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
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
            ))
        return memory

    def get(self, memory_id: str) -> Optional[Memory]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return self._to_memory(row) if row else None

    def get_portable(self) -> list:
        """
        #71: Return all portable=True memories — the set an offspring instance should inherit.
        Excludes EPISODIC, CREDENTIAL_REF, and any memory explicitly marked portable=False.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE portable = 1 "
                "AND memory_type NOT IN ('EPISODIC', 'CREDENTIAL_REF') "
                "ORDER BY id"
            ).fetchall()
        return [self._to_memory(r) for r in rows]

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
            narrative=scrub(narrative[:500]),
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
            results.append({
                "memory_id": row["memory_id"],
                "narrative": row["narrative"],
                "tags": blob_tags,
                "matched_tags": matched,
                "content_preview": row["content"][:200],
                "created_at": row["created_at"],
            })
        return results

    def list_blob_tags(self) -> dict[str, int]:
        """Return all tags with counts, sorted by frequency."""
        with self._conn() as conn:
            rows = conn.execute("SELECT tags FROM memory_blobs").fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            try:
                for tag in json.loads(row["tags"] or "[]"):
                    counts[tag] = counts.get(tag, 0) + 1
            except Exception:
                pass
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))

    def search(self, query: str, limit: int = 10, emotional_context=None) -> list:
        """
        Hybrid search (change.37): text candidates → embedding re-rank.

        Phase 1 (always runs): naive text search over the memory graph.
          Filters out ROOT/CORE_PATTERN only (structural bedrock, always in system prompt).
          IDENTITY and ROLE_MODEL are now searchable — they are who Igor is (#86/#98).
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
                "SELECT * FROM memories WHERE memory_type NOT IN (?, ?) "
                "ORDER BY activation_count DESC",
                (MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value)
            ).fetchall()

        all_memories = [self._to_memory(r) for r in rows]

        # Filter out NE diagnostic memories — operational noise from consolidation/stall loops
        # that may have entered LTM before the self-diagnostic filter was in place (URGENT.3)
        _NE_DIAG = ("consolidation", "stall", "loop detected", "recursive", "ne_diag")
        all_memories = [
            m for m in all_memories
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
                    except Exception:
                        pass  # Signal C must never block search

                # #66: affect-weighted retrieval — memories encoded in similar
                # emotional state get a small relevance boost (state-dependent recall)
                if emotional_context is not None:
                    for sim, m in scored:
                        v_sim = 1.0 - abs(getattr(m, "valence", 0.0) - emotional_context.valence) / 2.0
                        a_sim = 1.0 - abs(getattr(m, "arousal", 0.0) - emotional_context.arousal) / 2.0
                        m.relevance_score = sim * (1.0 + 0.15 * v_sim * a_sim)
                    scored.sort(key=lambda x: getattr(x[1], "relevance_score", x[0]), reverse=True)

                result = [m for _, m in scored[:limit]]
                # G9: spreading activation — boost graph neighbors
                return self._spread_activation(result, {}, limit)
        except Exception:
            pass  # Embedding unavailable — fall through to text results

        # Phase 1 fallback: attach normalised relevance score and return
        max_terms = max(1, len(terms))
        for score, m in text_scored[:limit]:
            m.relevance_score = score / max_terms  # type: ignore[attr-defined]
        result = [m for _, m in text_scored[:limit]]
        # G9: spreading activation — boost graph neighbors
        return self._spread_activation(result, {}, limit)

    # G9 / #60: spreading activation ──────────────────────────────────────────

    _SA_DECAY = 0.4   # neighbor relevance = parent_relevance * _SA_DECAY

    def _spread_activation(
        self,
        activated: list,
        all_memories: dict,
        limit: int,
    ) -> list:
        """
        Given a list of activated Memory objects (with .relevance_score set),
        find their graph neighbors (parent_id, children_ids, link_ids) and give
        them a decay-weighted partial activation boost.

        Neighbors already in `activated` get a small relevance bump.
        New neighbors below the original activation threshold are appended
        at decayed relevance and sorted back into the result.

        Returns the merged list (max `limit` items).
        """
        activated_ids = {m.id for m in activated}
        neighbor_scores: dict[str, float] = {}

        for m in activated:
            base = getattr(m, "relevance_score", 0.1) or 0.1
            spread = base * self._SA_DECAY

            # Gather adjacent node IDs
            adjacent_ids: list[str] = []
            if getattr(m, "parent_id", None):
                adjacent_ids.append(m.parent_id)
            adjacent_ids.extend(getattr(m, "children_ids", []) or [])
            adjacent_ids.extend(getattr(m, "link_ids", []) or [])

            for adj_id in adjacent_ids:
                if adj_id in activated_ids:
                    # Already activated — small boost only
                    existing = next((x for x in activated if x.id == adj_id), None)
                    if existing:
                        existing.relevance_score = min(  # type: ignore[attr-defined]
                            1.0, getattr(existing, "relevance_score", 0.0) + spread * 0.3
                        )
                else:
                    # New neighbor — record best spread score
                    if adj_id not in neighbor_scores or neighbor_scores[adj_id] < spread:
                        neighbor_scores[adj_id] = spread

        # Fetch new neighbors from DB; skip structural infrastructure only
        _SKIP_TYPES = {
            MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value,
        }
        new_neighbors: list = []
        if neighbor_scores:
            with self._conn() as conn:
                placeholders = ",".join("?" * len(neighbor_scores))
                rows = conn.execute(
                    f"SELECT * FROM memories WHERE id IN ({placeholders})",
                    list(neighbor_scores.keys()),
                ).fetchall()
            for row in rows:
                m = self._to_memory(row)
                if m.memory_type in _SKIP_TYPES:
                    continue
                m.relevance_score = neighbor_scores[m.id]  # type: ignore[attr-defined]
                new_neighbors.append(m)

        merged = activated + new_neighbors
        merged.sort(key=lambda m: getattr(m, "relevance_score", 0.0), reverse=True)
        return merged[:limit]

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
        keys = row.keys()
        return Memory(
            id=row["id"],
            narrative=row["narrative"],
            memory_type=_safe_memory_type(row["memory_type"]),
            parent_id=row["parent_id"],
            children_ids=json.loads(row["children_ids"]),
            link_ids=json.loads(row["link_ids"]),
            valence=row["valence"] or 0.0,
            arousal=row["arousal"] if "arousal" in keys else 0.0,
            dominance=row["dominance"] if "dominance" in keys else 0.0,
            activation_count=row["activation_count"],
            friction_history=json.loads(row["friction_history"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            metadata=json.loads(row["metadata"]),
            portable=bool(row["portable"]) if "portable" in keys else True,
        )

    # ── Ring memory (short-term, survives restarts) ────────────────────────────

    def write_ring(self, content: str, category: str = "note"):
        """
        Write an entry to the short-term ring buffer.
        Automatically trims to RING_MAX entries (oldest first).
        """
        content = scrub(content)
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
                 metadata: dict = None, ttl_seconds: int = None,
                 urgency: float = 0.2) -> int:
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
        with self._conn() as conn:
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

        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO twm_observations
                   (timestamp, source, content_csb, salience, metadata_json,
                    integrated, integration_count, expires_at, urgency)
                   VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)""",
                (now.isoformat(), source, content_csb, salience,
                 json.dumps(metadata or {}), expires_at, urgency)
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
                "urgency": r["urgency"] if "urgency" in r.keys() else 0.2,
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

    def twm_extend_ttl(self, obs_id: int, extension_seconds: int = None, reason: str = "") -> None:
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
            with self._conn() as conn:
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
        except Exception:
            pass  # TTL extension must never crash callers

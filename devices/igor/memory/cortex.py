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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .models import Memory, MemoryType
from .scrub import scrub
from .db_proxy import DatabaseProxy


def _safe_memory_type(value: str) -> MemoryType:
    """Return MemoryType for value, falling back to FACTUAL for unknown types."""
    try:
        return MemoryType(value)
    except ValueError:
        return MemoryType.FACTUAL


RING_MAX = 50  # Max entries in the ring buffer
TWM_MAX  = 50  # Max observations in TWM
# G47: suppress repeated observations at the door rather than admitting at floor salience.
TWM_SUPPRESS_AFTER_REPEATS = int(os.getenv("IGOR_TWM_SUPPRESS_REPEATS", "4"))
TWM_SUPPRESS_SALIENCE_FLOOR = float(os.getenv("IGOR_TWM_SUPPRESS_FLOOR", "0.04"))

# Change 4: urgency — distinct from salience (time-sensitivity vs importance)
# Change 3: TTL extension on confirmed relevance (not mere access)
TWM_TTL_EXTENSION_SECONDS = int(
    __import__("os").getenv("TWM_TTL_EXTENSION_SECONDS", "1800")
)


class Cortex:
    """SQLite-backed memory graph."""

    def __init__(self, db_path: Path, instance_id: str = None):
        self.db_path = db_path
        self._instance_id = instance_id  # #51: scopes TWM to this instance when set
        self._db = DatabaseProxy(db_path)
        self._init_db()

    def _conn(self):
        """Deprecated shim — use self._db() directly."""
        return self._db()

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

            # #128: directed weighted links + last_accessed
            for _col in (
                "links_weighted TEXT DEFAULT '{}'",
                "last_accessed TEXT DEFAULT NULL",
            ):
                try:
                    conn.execute(f"ALTER TABLE memories ADD COLUMN {_col}")
                except Exception:
                    pass  # Column already exists

            # G46: provenance + epistemic fields
            for _col in (
                "source TEXT DEFAULT ''",
                "confidence REAL DEFAULT 1.0",
                "context_of_encoding TEXT DEFAULT ''",
            ):
                try:
                    conn.execute(f"ALTER TABLE memories ADD COLUMN {_col}")
                except Exception:
                    pass  # Column already exists

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
                except Exception:
                    pass

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
                conn.execute("ALTER TABLE ring_memory ADD COLUMN thread_id TEXT DEFAULT NULL")
            except Exception:
                pass  # Column already exists

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

            # #51: instance_id column — scopes each observation to the instance that pushed it
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN instance_id TEXT DEFAULT NULL"
                )
            except Exception:
                pass  # Column already exists

            # #158: thread_id — per-attention-nexus isolation (mirrors ring_memory #136)
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN thread_id TEXT DEFAULT NULL"
                )
            except Exception:
                pass

            # G50: attractor_weight — the current primary focus; one item typically non-zero
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN attractor_weight REAL DEFAULT 0.0"
                )
            except Exception:
                pass

            # #158: category — distinguishes TASK_SET from normal observations
            try:
                conn.execute(
                    "ALTER TABLE twm_observations ADD COLUMN category TEXT DEFAULT 'observation'"
                )
            except Exception:
                pass

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
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO memories
                (id, narrative, memory_type, parent_id, children_ids, link_ids,
                 valence, arousal, dominance,
                 activation_count, friction_history, timestamp, metadata, portable,
                 links_weighted, last_accessed,
                 source, confidence, context_of_encoding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(memory.links),
                memory.last_accessed.isoformat() if memory.last_accessed else None,
                memory.source,
                memory.confidence,
                memory.context_of_encoding,
            ))
        # #170: auto-connect new INTERPRETIVE memories to the nearest CP.
        # Keyword affinity → no LLM needed; never blocks store on failure.
        if memory.memory_type == MemoryType.INTERPRETIVE:
            try:
                self._auto_wire_interpretive(memory)
            except Exception:
                pass  # auto-wire must never interrupt a store
        return memory

    # CP keyword affinity table for auto-wiring (#170)
    _CP_KEYWORDS: dict = {
        "CP1": ["don't know", "uncertain", "unknown", "honest", "epistemic", "truth", "ignorance"],
        "CP2": ["fail", "failure", "learn", "obstacle", "blocked", "mistake", "error", "wrong", "emerge"],
        "CP3": ["why", "reason", "understand", "structure", "meaning", "motivation", "purpose", "cause"],
        "CP4": ["friction", "usability", "design", "easier", "interface", "accessible", "suck less"],
        "CP5": ["experience", "emotion", "respect", "person", "human", "feel", "interpersonal", "consciousness"],
        "CP6": ["safe", "safety", "risk", "danger", "protect", "critical", "secure", "guard"],
    }

    def _auto_wire_interpretive(self, memory: "Memory") -> None:
        """
        #170: Find the best-matching CP for a new INTERPRETIVE memory and create
        an activation edge if none exists yet.  Pure keyword scoring — zero LLM cost.
        Skips SESSION_SUMMARY noise entries.
        """
        narrative_lower = memory.narrative.lower()
        # Skip operational logs masquerading as INTERPRETIVE
        if narrative_lower.startswith("session_summary") or narrative_lower.startswith("fallback:"):
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

    def reinforce_links(self, memory_id: str, co_active_ids: list, delta: float) -> None:
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
            results.append({
                "memory_id": row["memory_id"],
                "narrative": row["narrative"],
                "tags": blob_tags,
                "matched_tags": matched,
                "content_preview": row["content"][:200],
                "created_at": row["created_at"],
            })
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
            except Exception:
                pass
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
            except Exception:
                pass
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))

    def search(self, query: str, limit: int = 10, emotional_context=None) -> list:
        """
        Three-phase hybrid search (#172 + change.37).

        Phase 0 — traversal-first (#172, always runs):
          If TWM has an active attractor, follow graph edges (parent/children/links)
          from anchor memory nodes to depth=2. Produces association-chain candidates
          before any similarity computation.

        Phase 1 — text scoring (always runs):
          Naive keyword search over all non-structural memories. Results merged with
          Phase 0 candidates; deduped; higher score wins for memories in both sets.

        Phase 2 — embedding re-rank (runs when Ollama available):
          Embed the query; cosine-rank the merged candidate pool. Falls back silently
          to the pre-ranked pool if nomic-embed-text is unavailable.
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

        # Phase 0: traversal-first from TWM anchors (#172)
        _traversal: list = []
        try:
            _anchors = self._get_context_anchors()
            if _anchors:
                _traversal = self._traversal_search(_anchors, depth=2, limit=limit * 2)
        except Exception:
            pass  # Traversal must never block search

        # Candidate pool: merge traversal + text results, dedup by id (#172)
        # Traversal memories are included regardless of keyword hit;
        # text-only memories fill gaps. In both → take the higher score.
        _max_terms = max(1, len(terms))
        _trav_map: dict[str, "Memory"] = {m.id: m for m in _traversal}
        _merged: dict[str, "Memory"] = dict(_trav_map)
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
        )[: limit * 2]

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
                result = self._spread_activation(result, {}, limit)
                self._apply_recency_frequency_boost(result)
                return result
        except Exception:
            pass  # Embedding unavailable — fall through to text results

        # Phase 1 fallback: candidates already scored + merged (#172); return top N
        result = candidates[:limit]
        # G9: spreading activation — boost graph neighbors
        result = self._spread_activation(result, {}, limit)
        self._apply_recency_frequency_boost(result)
        return result

    def _apply_recency_frequency_boost(self, memories: list) -> None:
        """#128 + G45: apply small recency, frequency, inertia, and confidence multipliers."""
        now = datetime.now()
        for m in memories:
            score = getattr(m, "relevance_score", 0.0) or 0.0
            # Recency: decays over 30 days, max +15%
            if m.last_accessed:
                days = max(0.0, (now - m.last_accessed).total_seconds() / 86400)
                recency = max(0.0, 1.0 - days / 30.0)
                score *= (1.0 + 0.15 * recency)
            # Frequency: caps at 20 activations, max +10%
            freq = min(1.0, m.activation_count / 20.0)
            score *= (1.0 + 0.10 * freq)
            # G45: inertia weighting — established memories slightly preferred [0.90, 1.05]
            # Low-inertia episodics (0.20) get -10%; high-inertia core patterns (0.95) get +4%
            score *= (0.90 + 0.15 * m.inertia)
            # G45: confidence weighting (G46 field) — uncertain memories slightly penalized [0.90, 1.00]
            confidence = getattr(m, "confidence", 1.0) or 1.0
            score *= (0.90 + 0.10 * confidence)
            m.relevance_score = score  # type: ignore[attr-defined]

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

            # Gather adjacent node IDs with their spread amounts
            # #128: weighted links use link weight × decay; unweighted use flat decay
            spread_map: dict[str, float] = {}

            if getattr(m, "parent_id", None):
                spread_map[m.parent_id] = spread
            for cid in (getattr(m, "children_ids", []) or []):
                spread_map[cid] = spread

            # Weighted directed links (new) — spread proportional to weight
            for link_id, link_weight in (getattr(m, "links", {}) or {}).items():
                weighted_spread = base * link_weight * self._SA_DECAY
                spread_map[link_id] = max(spread_map.get(link_id, 0.0), weighted_spread)

            # Legacy link_ids — use flat decay, don't double-count if already in links
            existing_links = set(getattr(m, "links", {}) or {})
            for lid in (getattr(m, "link_ids", []) or []):
                if lid not in existing_links:
                    spread_map[lid] = max(spread_map.get(lid, 0.0), spread)

            for adj_id, adj_spread in spread_map.items():
                if adj_id in activated_ids:
                    # Already activated — small boost only
                    existing = next((x for x in activated if x.id == adj_id), None)
                    if existing:
                        existing.relevance_score = min(  # type: ignore[attr-defined]
                            1.0, getattr(existing, "relevance_score", 0.0) + adj_spread * 0.3
                        )
                else:
                    # New neighbor — record best spread score
                    if adj_id not in neighbor_scores or neighbor_scores[adj_id] < adj_spread:
                        neighbor_scores[adj_id] = adj_spread

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
        except Exception:
            pass

        # 3: Recent TWM items with explicit memory_id in metadata
        try:
            recent = self.twm_read(limit=10, include_integrated=False)
            for obs in sorted(recent, key=lambda x: x.get("salience", 0.0), reverse=True)[:5]:
                mid = (obs.get("metadata") or {}).get("memory_id")
                if mid and mid not in seen:
                    anchors.append(mid)
                    seen.add(mid)
        except Exception:
            pass

        return anchors[:5]

    def _traversal_search(
        self,
        anchor_ids: list[str],
        depth: int = 2,
        limit: int = 20,
    ) -> list:
        """
        #172: BFS from anchor_ids, following all edge types (parent, children, links).
        Returns Memory objects with relevance_score = decay-weighted path score.
        Anchor nodes themselves are included at score 1.0.
        """
        _SKIP = {MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value}
        visited: dict[str, float] = {mid: 1.0 for mid in anchor_ids}
        frontier: list[tuple[str, float]] = [(mid, 1.0) for mid in anchor_ids]

        for _hop in range(depth):
            if not frontier:
                break
            ids = [fid for fid, _ in frontier]
            with self._conn() as conn:
                placeholders = ",".join("?" * len(ids))
                rows = conn.execute(
                    f"SELECT * FROM memories WHERE id IN ({placeholders})", ids
                ).fetchall()
            mem_map = {row["id"]: self._to_memory(row) for row in rows}

            next_frontier: list[tuple[str, float]] = []
            for fid, fscore in frontier:
                m = mem_map.get(fid)
                if m is None:
                    continue
                neighbors: dict[str, float] = {}
                decay = self._SA_DECAY
                if getattr(m, "parent_id", None):
                    neighbors[m.parent_id] = fscore * decay
                for cid in (getattr(m, "children_ids", []) or []):
                    neighbors[cid] = max(neighbors.get(cid, 0.0), fscore * decay)
                for lid, lw in (getattr(m, "links", {}) or {}).items():
                    neighbors[lid] = max(neighbors.get(lid, 0.0), fscore * float(lw) * decay)
                existing_links = set(getattr(m, "links", {}) or {})
                for lid in (getattr(m, "link_ids", []) or []):
                    if lid not in existing_links:
                        neighbors[lid] = max(neighbors.get(lid, 0.0), fscore * decay)
                for nid, nscore in neighbors.items():
                    if nscore > visited.get(nid, 0.0):
                        visited[nid] = nscore
                        next_frontier.append((nid, nscore))
            frontier = next_frontier

        if len(visited) <= len(anchor_ids):
            return []  # No traversal beyond anchors — graph likely sparse

        all_ids = list(visited.keys())
        with self._conn() as conn:
            placeholders = ",".join("?" * len(all_ids))
            rows = conn.execute(
                f"SELECT * FROM memories WHERE id IN ({placeholders})", all_ids
            ).fetchall()

        results = []
        for row in rows:
            m = self._to_memory(row)
            if m.memory_type in _SKIP:
                continue
            m.relevance_score = visited.get(m.id, 0.0)  # type: ignore[attr-defined]
            results.append(m)
        results.sort(key=lambda m: getattr(m, "relevance_score", 0.0), reverse=True)
        return results[:limit]

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
        # #128: any memory with a trigger field is a habit — not gated on PROCEDURAL type
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE metadata LIKE '%\"trigger\"%'"
            ).fetchall()
        return [m for m in (self._to_memory(r) for r in rows) if m.is_habit]

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

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, narrative FROM memories "
                "WHERE embedding IS NULL OR embedding = 'null'"
            ).fetchall()

        updated = 0
        for row in rows:
            text = (row["narrative"] or "").strip()
            if not text:
                continue
            try:
                vec = _embed(text)
                if vec:
                    with self._conn() as conn:
                        conn.execute(
                            "UPDATE memories SET embedding = ? WHERE id = ?",
                            (_json.dumps(vec), row["id"]),
                        )
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
        # #128: load directed weighted links
        _links = {}
        if "links_weighted" in keys:
            try:
                _links = json.loads(row["links_weighted"] or "{}")
            except Exception:
                pass
        # #128: load last_accessed
        _last_accessed = None
        if "last_accessed" in keys and row["last_accessed"]:
            try:
                _last_accessed = datetime.fromisoformat(row["last_accessed"])
            except Exception:
                pass
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
            metadata=json.loads(row["metadata"]),
            portable=bool(row["portable"]) if "portable" in keys else True,
            # G46: provenance + epistemic fields
            source=row["source"] if "source" in keys and row["source"] else "",
            confidence=float(row["confidence"]) if "confidence" in keys and row["confidence"] is not None else 1.0,
            context_of_encoding=row["context_of_encoding"] if "context_of_encoding" in keys and row["context_of_encoding"] else "",
        )

    # ── Ring memory (short-term, survives restarts) ────────────────────────────

    def write_ring(self, content: str, category: str = "note", thread_id: str | None = None):
        """
        Write an entry to the short-term ring buffer.
        Automatically trims to RING_MAX entries (oldest first).

        thread_id: optional per-channel key (e.g. "discord:123456") for #136 P2 isolation.
        None = global entry visible to all threads.
        """
        content = scrub(content)
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO ring_memory (category, content, timestamp, thread_id) VALUES (?, ?, ?, ?)",
                (category, content, now, thread_id)
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
        with self._conn() as conn:
            if category and thread_id:
                rows = conn.execute(
                    "SELECT * FROM ring_memory WHERE category = ? "
                    "AND (thread_id = ? OR thread_id IS NULL) "
                    "ORDER BY id DESC LIMIT ?",
                    (category, thread_id, limit)
                ).fetchall()
            elif thread_id:
                rows = conn.execute(
                    "SELECT * FROM ring_memory "
                    "WHERE (thread_id = ? OR thread_id IS NULL) "
                    "ORDER BY id DESC LIMIT ?",
                    (thread_id, limit)
                ).fetchall()
            elif category:
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
        entries = [
            {
                "id": r["id"], "category": r["category"],
                "content": r["content"], "timestamp": r["timestamp"],
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
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM ring_memory WHERE {clauses} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [
            {
                "id": r["id"], "category": r["category"],
                "content": r["content"], "timestamp": r["timestamp"],
                "thread_id": r["thread_id"] if "thread_id" in r.keys() else None,
            }
            for r in rows
        ]

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
                 urgency: float = 0.2, thread_id: str | None = None,
                 category: str = "observation") -> int:
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

        # G47: suppress at the door — don't admit observations that are too noisy to matter.
        # Prevents low-salience spam (e.g. NE impulse loop, repeated resource warnings)
        # from filling TWM slots that should go to meaningful context.
        if urgency < 0.65:  # high-urgency (ethics, inbox, user input) always admitted
            if _repeat_count >= TWM_SUPPRESS_AFTER_REPEATS:
                return -1  # suppressed — caller can ignore this return value
            if salience < TWM_SUPPRESS_SALIENCE_FLOOR:
                return -1  # suppressed

        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO twm_observations
                   (timestamp, source, content_csb, salience, metadata_json,
                    integrated, integration_count, expires_at, urgency, instance_id,
                    thread_id, category, attractor_weight)
                   VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, 0.0)""",
                (now.isoformat(), source, content_csb, salience,
                 json.dumps(metadata or {}), expires_at, urgency, self._instance_id,
                 thread_id, category)
            )
            obs_id = cur.lastrowid

            # G50: high-urgency items (inbox, ethics, user input ≥0.8) become attractor
            if urgency >= 0.8 and obs_id and obs_id > 0:
                conn.execute(
                    "UPDATE twm_observations SET attractor_weight = 0.0 "
                    "WHERE instance_id = ? AND id != ?",
                    (self._instance_id, obs_id)
                )
                conn.execute(
                    "UPDATE twm_observations SET attractor_weight = 1.0 WHERE id = ?",
                    (obs_id,)
                )

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

    def twm_read(self, limit: int = 50, include_integrated: bool = True,
                 thread_id: str | None = None,
                 category: str | None = None) -> list[dict]:
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

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM twm_observations {where} ORDER BY id ASC LIMIT ?",
                params
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
                "attractor_weight": r["attractor_weight"] if "attractor_weight" in r.keys() else 0.0,
            }
            for r in rows
        ]

    # ── G50: TWM Attractor ─────────────────────────────────────────────────────

    def twm_set_attractor(self, obs_id: int, weight: float = 1.0) -> None:
        """
        G50: Set one TWM item as the current primary attractor.
        Clears attractor_weight on all other items for this instance first.
        Callers: UserInputSource.push_message(), high-priority push_sources.

        The attractor represents the current primary focus — the question or task
        that gives direction to tree traversal. It shapes which TWM items the NE
        and context builders weight most heavily.
        """
        if obs_id <= 0:
            return
        with self._conn() as conn:
            conn.execute(
                "UPDATE twm_observations SET attractor_weight = 0.0 "
                "WHERE instance_id = ? AND id != ?",
                (self._instance_id, obs_id)
            )
            conn.execute(
                "UPDATE twm_observations SET attractor_weight = ? WHERE id = ?",
                (min(1.0, max(0.0, weight)), obs_id)
            )

    def twm_get_attractor(self) -> dict | None:
        """
        G50: Return the current attractor TWM item (highest attractor_weight > 0.1),
        or None if no attractor is active.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM twm_observations "
                "WHERE instance_id = ? AND attractor_weight > 0.1 "
                "ORDER BY attractor_weight DESC LIMIT 1",
                (self._instance_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "content_csb": row["content_csb"],
            "attractor_weight": row["attractor_weight"],
            "salience": row["salience"],
            "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},  # #172
        }

    def twm_decay_attractor(self, factor: float = 0.90) -> None:
        """
        G50: Decay all attractor_weights by factor. Call from HeartbeatSource (every 5 min).
        factor=0.90 → attractor fades to ~0.1 after ~22 heartbeats (~110 minutes).
        Below 0.05 is treated as inactive.
        """
        with self._conn() as conn:
            conn.execute(
                "UPDATE twm_observations "
                "SET attractor_weight = attractor_weight * ? "
                "WHERE instance_id = ? AND attractor_weight > 0.05",
                (factor, self._instance_id)
            )
            # Zero out anything that has decayed below threshold
            conn.execute(
                "UPDATE twm_observations SET attractor_weight = 0.0 "
                "WHERE instance_id = ? AND attractor_weight <= 0.05",
                (self._instance_id,)
            )

    def twm_clear_task_set(self, thread_id: str | None = None) -> int:
        """
        #158: Mark all TASK_SET entries for this thread as integrated (completed).
        Called when a task completion signal is detected in the response.
        Returns count of entries cleared.
        """
        with self._conn() as conn:
            if thread_id:
                result = conn.execute(
                    "UPDATE twm_observations SET integrated = 1 "
                    "WHERE category = 'task_set' AND integrated = 0 "
                    "AND (thread_id = ? OR thread_id IS NULL)",
                    (thread_id,)
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
        with self._conn() as conn:
            if _iid:
                return conn.execute(
                    "SELECT COUNT(*) FROM twm_observations WHERE integrated = 0 AND instance_id = ?",
                    (_iid,)
                ).fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM twm_observations WHERE integrated = 0"
            ).fetchone()[0]

    def twm_count(self) -> int:
        """Total TWM observation rows (fingerprint helper for NE idle gate)."""
        _iid = self._instance_id
        with self._conn() as conn:
            if _iid:
                return conn.execute(
                    "SELECT COUNT(*) FROM twm_observations WHERE instance_id = ?",
                    (_iid,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM twm_observations").fetchone()[0]

    def twm_max_id(self) -> int:
        """Highest TWM observation id (fingerprint helper for NE idle gate)."""
        _iid = self._instance_id
        with self._conn() as conn:
            if _iid:
                row = conn.execute(
                    "SELECT MAX(id) FROM twm_observations WHERE instance_id = ?",
                    (_iid,)
                ).fetchone()
            else:
                row = conn.execute("SELECT MAX(id) FROM twm_observations").fetchone()
            return row[0] if row and row[0] is not None else 0

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
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE memory_type='INTERPRETIVE' "
                "AND metadata LIKE '%\"source\": \"relational\"%'"
            ).fetchall()
        nodes = [self._to_memory(r) for r in rows]
        return [
            n for n in nodes
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
            if node_name in text_lower or any(w in text_lower for w in narrative_words if len(w) > 3):
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
                new_meta["last_decay"] = __import__("datetime").datetime.utcnow().isoformat()
                try:
                    import json as _json
                    with self._conn() as conn:
                        conn.execute(
                            "UPDATE memories SET metadata=? WHERE id=?",
                            (_json.dumps(new_meta), node.id),
                        )
                    updated += 1
                except Exception:
                    pass

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
    ) -> int:
        """
        G52: Add a directed edge between two memories in the interpretive tree.

        Edge semantics (4 parts):
          direction: "activation" | "inhibition" — does traversal promote or suppress?
          condition_csb: CSB string specifying when this edge fires (empty = always)
          meaning_payload: the WHY — what reaching to_id means about self or situation
          action_pointer: memory id or code_ref of the next tree to explore
          weight: traversal strength [0,1]

        CP1-CP6 are root nodes. Their children are the first interpretive layer.
        Returns the new edge id.
        """
        from datetime import datetime as _dt
        now = _dt.now().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO interpretive_edges
                    (from_id, to_id, direction, condition_csb, meaning_payload, action_pointer, weight, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (from_id, to_id, direction, condition_csb, meaning_payload, action_pointer,
                 max(0.0, min(1.0, weight)), now),
            )
            return cur.lastrowid

    def get_interpretive_edges(self, from_id: str) -> list[dict]:
        """
        G52: Return all outgoing interpretive edges from from_id.
        Each dict: {id, from_id, to_id, direction, condition_csb, meaning_payload, action_pointer, weight}
        Ordered by weight DESC.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, from_id, to_id, direction, condition_csb,
                       meaning_payload, action_pointer, weight, created_at
                FROM interpretive_edges
                WHERE from_id = ?
                ORDER BY weight DESC
                """,
                (from_id,),
            ).fetchall()
        return [dict(r) for r in rows]

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
            condition_csb=f"temporal_sequence:{context}" if context else "temporal_sequence",
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
        """
        if not from_ids:
            return []

        _milieu_bias: dict = milieu_bias or {}
        visited: set[str] = set(from_ids)
        queue: list[tuple[str, int, str]] = [(fid, 0, fid) for fid in from_ids]  # (id, depth, root)
        result_ids: list[str] = []
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
                    # #182: convergence check — is this node a lever?
                    _is_convergence = False
                    if exit_on_convergence:
                        try:
                            _to_mem = self.get(edge["to_id"])
                            if _to_mem:
                                _iw = (_to_mem.metadata or {}).get("investment_weight", 0.0)
                                if _iw >= convergence_weight:
                                    _is_convergence = True
                                else:
                                    # check out-degree
                                    if edge["to_id"] not in _out_degree_cache:
                                        with self._conn() as _c:
                                            _out_degree_cache[edge["to_id"]] = _c.execute(
                                                "SELECT COUNT(*) FROM interpretive_edges WHERE from_id=?",
                                                (edge["to_id"],),
                                            ).fetchone()[0]
                                    if _out_degree_cache[edge["to_id"]] >= convergence_out_degree:
                                        _is_convergence = True
                        except Exception:
                            pass
                    if not _is_convergence:
                        queue.append((edge["to_id"], depth + 1, root_id))
                    # convergence node is collected but not descended — it's the lever

        if not result_ids:
            return []

        # Fetch the actual Memory objects
        from .models import Memory as _M  # avoid circular at module level
        placeholders = ",".join("?" * len(result_ids))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM memories WHERE id IN ({placeholders})",
                result_ids,
            ).fetchall()
        row_by_id = {r["id"]: r for r in rows}
        # Return in traversal order
        memories = []
        for mid in result_ids:
            if mid in row_by_id:
                mem = self._to_memory(row_by_id[mid])
                if mem:
                    memories.append(mem)
        return memories

"""
Cortex - long-term memory storage.
SQLite-backed graph of Memory objects.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Memory, MemoryType


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
        """Naive text search. Spreading activation comes later."""
        terms = query.lower().split()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE memory_type NOT IN (?, ?, ?, ?) ORDER BY activation_count DESC",
                (MemoryType.ROOT.value, MemoryType.CORE_PATTERN.value,
                 MemoryType.IDENTITY.value, MemoryType.ROLE_MODEL.value)
            ).fetchall()

        memories = [self._to_memory(r) for r in rows]
        scored = []
        for m in memories:
            score = sum(1 for t in terms if t in m.narrative.lower())
            if score > 0:
                scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:limit]]

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

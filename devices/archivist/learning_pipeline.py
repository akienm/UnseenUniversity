"""
InferenceAccumulator — in-memory accumulator for archivist inference miss payloads.

Receives (query, response) pairs from ArchivistProxy on cache misses and
accumulates them for overnight processing into archivist.knowledge_patterns.
Processing is a stub until the pattern-compiler ships.

Distinct from devices/librarian/learning_pipeline.py (LearningPipeline), which
is the canonical knowledge-graph builder writing to clan.memories. These serve
different schemas and different purposes (T-resolve-dual-learning-pipeline).

Graph store schema (Postgres, no SQLite):
  archivist.knowledge_patterns  — compiled query patterns (the graph nodes)
  archivist.learning_queue      — pending payloads for overnight processing

Schema is defined here so migrations can reference a single authoritative source.
"""

from __future__ import annotations

import collections
import logging

log = logging.getLogger(__name__)

# ── Postgres schema ───────────────────────────────────────────────────────────

GRAPH_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS archivist;

CREATE TABLE IF NOT EXISTS archivist.knowledge_patterns (
    id BIGSERIAL PRIMARY KEY,
    pattern_hash TEXT NOT NULL UNIQUE,
    pattern_text TEXT NOT NULL,
    response_text TEXT NOT NULL,
    hit_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_hit_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS archivist.learning_queue (
    id BIGSERIAL PRIMARY KEY,
    query_text TEXT NOT NULL,
    response_text TEXT NOT NULL,
    model TEXT,
    caller TEXT,
    session_id TEXT,
    enqueued_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);
"""

# Backward-compat alias — callers that used the old class name still work.
# Rename target: InferenceAccumulator (T-resolve-dual-learning-pipeline).
LearningPipeline = None  # replaced below


class InferenceAccumulator:
    """In-memory accumulator for archivist inference miss payloads.

    Stub phase: enqueue() records misses; process_overnight() will compile
    them into archivist.knowledge_patterns once the pattern-compiler ships.
    Canonical knowledge-graph builder is devices/librarian/learning_pipeline.py.
    """

    def __init__(self) -> None:
        self._queue: collections.deque[dict] = collections.deque()

    def enqueue(self, payload: dict) -> None:
        """Add a learning payload to the overnight queue."""
        self._queue.append(payload)
        log.info(
            "learning: payload enqueued (queue_depth=%d caller=%s)",
            len(self._queue),
            payload.get("caller", ""),
        )

    def queue_depth(self) -> int:
        return len(self._queue)

    def process_overnight(self) -> int:
        """Stub — drains queue, returns count. Full compiler ships later."""
        count = len(self._queue)
        log.info("learning: overnight processing started (payload_count=%d)", count)
        self._queue.clear()
        log.info("learning: overnight processing complete (processed=%d)", count)
        return count


# Alias for callers that still import LearningPipeline from this module.
LearningPipeline = InferenceAccumulator

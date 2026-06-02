"""
LearningPipeline — overnight knowledge-graph building pipeline for the Archivist.

Receives learning payloads from the proxy (query + response pairs) and
accumulates them for overnight processing. Processing stub for now — the
graph build logic ships in T-inference-learning-pipeline.

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


class LearningPipeline:
    """
    Accumulates inference miss payloads for overnight graph-tree building.

    Queue is in-memory for the stub phase. Overnight processing converts
    accumulated (query, response) pairs into archivist.knowledge_patterns rows,
    progressively absorbing more queries locally.
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
        """
        Process all queued payloads into the knowledge graph.

        Stub — logs intent, drains queue, returns count. Full graph-build
        logic ships in T-inference-learning-pipeline.
        """
        count = len(self._queue)
        log.info("learning: overnight processing started (payload_count=%d)", count)
        self._queue.clear()
        log.info("learning: overnight processing complete (processed=%d)", count)
        return count

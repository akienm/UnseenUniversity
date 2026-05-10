"""
activate.py — spreading activation primitive for clan.memories.

Steps per call:
  1. Fetch node; compute cosine similarity vs stimulus_embedding.
  2. Apply temporal decay (0.7 per day) to existing activation_score.
  3. new_score = decayed_old + similarity * signal_weight.
  4. Persist activation_score + last_activated_at.
  5. Propagate to parent + associations (visited-set + depth cap).
  6. At depth=0, call focus_state.update_from_activation() if available.

T-igor-activate-primitive / D-activate-primitive-2026-05-10
"""

from __future__ import annotations

import logging
import math
import os

log = logging.getLogger(__name__)

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_MIGRATE_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='clan' AND table_name='memories'
          AND column_name='activation_score'
    ) THEN
        ALTER TABLE clan.memories ADD COLUMN activation_score float DEFAULT 0.0;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='clan' AND table_name='memories'
          AND column_name='last_activated_at'
    ) THEN
        ALTER TABLE clan.memories ADD COLUMN last_activated_at timestamptz;
    END IF;
END$$;
"""


def _get_conn():
    import psycopg2

    return psycopg2.connect(_PG_URL)


def _ensure_migration(conn) -> None:
    with conn:
        with conn.cursor() as cur:
            cur.execute(_MIGRATE_SQL)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _days_since(ts) -> float:
    """Return fractional days since a timestamp (datetime object or ISO string)."""
    if ts is None:
        return 0.0
    try:
        from datetime import datetime, timezone

        if not isinstance(ts, datetime):
            ts_str = str(ts).strip()
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - ts).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def activate(
    memory_id: str,
    stimulus_embedding: list[float],
    *,
    conn=None,
    **kwargs,
) -> float:
    """Activate a memory node and propagate spreading activation to neighbors.

    Returns the new activation_score for memory_id, or 0.0 if skipped.

    kwargs (all optional):
        threshold: float = 0.65        cosine similarity cutoff
        signal_weight: float = 1.0     stimulus signal weight
        propagation_decay: float = 0.7 multiplier per hop
        max_depth: int = 3             hop limit
        min_score: float = 0.05        propagation cutoff
        _visited: set[str]             internal recursion guard
        _depth: int                    internal depth counter
        _top_node: list                internal [id, score] for focus_state
    """
    threshold = float(kwargs.get("threshold", 0.65))
    signal_weight = float(kwargs.get("signal_weight", 1.0))
    propagation_decay = float(kwargs.get("propagation_decay", 0.7))
    max_depth = int(kwargs.get("max_depth", 3))
    min_score = float(kwargs.get("min_score", 0.05))
    visited: set[str] = kwargs.get("_visited") or set()
    depth: int = int(kwargs.get("_depth", 0))
    top_node: list = kwargs.get("_top_node") or [None, 0.0]

    if memory_id in visited or depth > max_depth:
        return 0.0
    visited.add(memory_id)

    _close_conn = conn is None
    if _close_conn:
        conn = _get_conn()
        _ensure_migration(conn)

    try:
        import json as _json
        import psycopg2.extras

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, metadata, activation_score, last_activated_at, parent_id "
                "FROM clan.memories WHERE id = %s",
                (memory_id,),
            )
            row = cur.fetchone()

        if row is None:
            return 0.0

        node = dict(row)
        metadata = node.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = _json.loads(metadata)
            except Exception:
                metadata = {}

        # --- Cosine similarity check ---
        watch_embedding = metadata.get("watch_embedding")
        if watch_embedding:
            if isinstance(watch_embedding, str):
                try:
                    watch_embedding = _json.loads(watch_embedding)
                except Exception:
                    watch_embedding = None
        if watch_embedding:
            similarity = _cosine_similarity(stimulus_embedding, watch_embedding)
            node_threshold = float(metadata.get("activation_threshold", threshold))
            if similarity < node_threshold:
                return 0.0
        else:
            similarity = 1.0

        # --- Temporal decay + new score ---
        old_score = float(node.get("activation_score") or 0.0)
        days = _days_since(node.get("last_activated_at"))
        decay_factor = 0.7**days if days > 0 else 1.0
        new_score = max(old_score * decay_factor, 0.0) + (similarity * signal_weight)

        # --- Persist ---
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clan.memories "
                    "SET activation_score = %s, last_activated_at = now() "
                    "WHERE id = %s",
                    (new_score, memory_id),
                )

        if new_score > top_node[1]:
            top_node[0] = memory_id
            top_node[1] = new_score

        # --- Propagate ---
        parent_id = node.get("parent_id")
        if parent_id:
            prop_score = new_score * propagation_decay
            if prop_score >= min_score:
                activate(
                    parent_id,
                    stimulus_embedding,
                    conn=conn,
                    threshold=threshold,
                    signal_weight=prop_score,
                    propagation_decay=propagation_decay,
                    max_depth=max_depth,
                    min_score=min_score,
                    _visited=visited,
                    _depth=depth + 1,
                    _top_node=top_node,
                )

        for assoc in metadata.get("associations") or []:
            if not isinstance(assoc, dict):
                continue
            assoc_id = assoc.get("memory_id")
            assoc_weight = float(assoc.get("weight", 1.0))
            if not assoc_id:
                continue
            prop_score = new_score * propagation_decay * assoc_weight
            if prop_score < min_score:
                continue
            activate(
                assoc_id,
                stimulus_embedding,
                conn=conn,
                threshold=threshold,
                signal_weight=prop_score,
                propagation_decay=propagation_decay,
                max_depth=max_depth,
                min_score=min_score,
                _visited=visited,
                _depth=depth + 1,
                _top_node=top_node,
            )

        # --- Update focus_state at top level ---
        if depth == 0 and top_node[0] is not None:
            try:
                from . import focus_state as _fs

                _fs.update_from_activation(top_node[0], top_node[1])
            except Exception:
                pass  # T-igor-focus-state not yet implemented

        return new_score

    finally:
        if _close_conn:
            try:
                conn.close()
            except Exception:
                pass

"""
edge_maintenance.py — Librarian background edge consolidation service.

Runs independently of Igor. Responsibilities:
  - Hebbian strengthening: co-activated memory nodes develop stronger edges.
  - Typed edge_type on all edge creates/strengthens (controlled vocabulary).
  - Lazy migration: existing NULL edge_type → 'co-activates' on next pass.

Controlled vocabulary (edge_type):
  'co-activates'  — default for Hebbian (nodes fire together)
  'implements'    — node A is an implementation of concept B
  'contradicts'   — node A contradicts node B
  'derived_from'  — node A was derived/copied from node B

Constraint violations (unknown edge_type) log a warning and use default.

D-shared-memory-service-2026-05-28
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_VALID_EDGE_TYPES = frozenset(
    {"co-activates", "implements", "contradicts", "derived_from"}
)
_DEFAULT_EDGE_TYPE = "co-activates"

_HEBBIAN_THRESHOLD_DEFAULT = 3
_HEBBIAN_DELTA_DEFAULT = 0.1
_HEBBIAN_LOOKBACK_DEFAULT = 100
_CONSOLIDATION_INTERVAL_S = float(
    os.environ.get("LIBRARIAN_CONSOLIDATION_INTERVAL_S", "3600")
)


# ── Edge type validation ───────────────────────────────────────────────────────


def validate_edge_type(edge_type: str | None) -> str:
    """Return valid edge_type, defaulting to 'co-activates' for unknown values."""
    if edge_type in _VALID_EDGE_TYPES:
        return edge_type
    if edge_type:
        log.warning(
            "unknown edge_type %r — using default %r", edge_type, _DEFAULT_EDGE_TYPE
        )
    return _DEFAULT_EDGE_TYPE


# ── Hebbian strengthening ──────────────────────────────────────────────────────


def strengthen_coactivated_edges(
    conn,
    *,
    edge_type: str = _DEFAULT_EDGE_TYPE,
    threshold: int | None = None,
    delta: float | None = None,
    lookback: int | None = None,
) -> int:
    """Hebbian edge strengthening: nodes that fire together wire together.

    Queries recent clan.traces, counts co-activation pairs, and UPSERTs
    weighted edges in clan.interpretive_edges with the given edge_type.

    Lazy migration: existing edges with NULL edge_type are updated to
    'co-activates' on each pass.

    Returns count of edges created or strengthened.
    """
    edge_type = validate_edge_type(edge_type)
    t = (
        threshold
        if threshold is not None
        else int(os.getenv("IGOR_HEBBIAN_THRESHOLD", str(_HEBBIAN_THRESHOLD_DEFAULT)))
    )
    d = (
        delta
        if delta is not None
        else float(os.getenv("IGOR_HEBBIAN_DELTA", str(_HEBBIAN_DELTA_DEFAULT)))
    )
    lb = (
        lookback
        if lookback is not None
        else int(os.getenv("IGOR_DREAMING_LOOKBACK", str(_HEBBIAN_LOOKBACK_DEFAULT)))
    )

    try:
        # Fetch recent traces
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nodes FROM clan.traces ORDER BY recorded_at DESC LIMIT %s",
                (lb,),
            )
            rows = cur.fetchall()

        # Count co-activation pairs
        pair_counts: Counter = Counter()
        for (nodes_raw,) in rows:
            try:
                nodes = (
                    json.loads(nodes_raw) if isinstance(nodes_raw, str) else nodes_raw
                )
                node_ids = [
                    n["node_id"]
                    for n in nodes
                    if isinstance(n, dict) and "node_id" in n
                ]
            except Exception:
                continue
            for i, a in enumerate(node_ids):
                for b in node_ids[i + 1 :]:
                    pair_counts[tuple(sorted((a, b)))] += 1

        # Upsert edges for qualifying pairs
        count = 0
        with conn:
            with conn.cursor() as cur:
                for (a, b), freq in pair_counts.items():
                    if freq < t:
                        continue
                    cur.execute(
                        "UPDATE clan.interpretive_edges "
                        "SET weight = weight + %s, edge_type = %s "
                        "WHERE from_id = %s AND to_id = %s AND layer = 'hebbian'",
                        (d, edge_type, a, b),
                    )
                    if cur.rowcount == 0:
                        cur.execute(
                            "INSERT INTO clan.interpretive_edges "
                            "(from_id, to_id, weight, layer, edge_type, created_at) "
                            "VALUES (%s, %s, %s, 'hebbian', %s, now()::text)",
                            (a, b, d, edge_type),
                        )
                    count += 1

        log.info(
            "edge_maintenance: hebbian — %d traces, %d edges (threshold=%d, delta=%.2f)",
            len(rows),
            count,
            t,
            d,
        )
        return count

    except Exception as _e:
        log.warning("strengthen_coactivated_edges failed (non-fatal): %s", _e)
        return 0


# ── Lazy migration ─────────────────────────────────────────────────────────────


def backfill_null_edge_types(conn) -> int:
    """Set edge_type='co-activates' on all edges where edge_type IS NULL."""
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clan.interpretive_edges "
                    "SET edge_type = %s "
                    "WHERE edge_type IS NULL",
                    (_DEFAULT_EDGE_TYPE,),
                )
                updated = cur.rowcount
        log.info(
            "edge_maintenance: backfill — %d edges updated to 'co-activates'", updated
        )
        return updated
    except Exception as _e:
        log.warning("backfill_null_edge_types failed: %s", _e)
        return 0


# ── Edge type query ────────────────────────────────────────────────────────────


def query_edges_by_type(conn, edge_type: str) -> list[dict]:
    """Return all edges with the given edge_type."""
    edge_type = validate_edge_type(edge_type)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT from_id, to_id, weight, layer, edge_type "
                "FROM clan.interpretive_edges "
                "WHERE edge_type = %s "
                "ORDER BY weight DESC",
                (edge_type,),
            )
            return [
                {
                    "from_id": r[0],
                    "to_id": r[1],
                    "weight": r[2],
                    "layer": r[3],
                    "edge_type": r[4],
                }
                for r in cur.fetchall()
            ]
    except Exception as _e:
        log.warning("query_edges_by_type failed: %s", _e)
        return []


# ── Consolidation cycle ────────────────────────────────────────────────────────


def run_consolidation(db_url: str | None = None) -> dict:
    """Run one full consolidation pass: Hebbian + lazy migration.

    Returns summary dict with counts.
    """
    import psycopg2

    url = db_url or os.environ.get("UU_HOME_DB_URL", "")
    if not url:
        return {"error": "no db_url", "hebbian_count": 0, "backfill_count": 0}

    try:
        conn = psycopg2.connect(url)
        hebbian_count = strengthen_coactivated_edges(conn)
        backfill_count = backfill_null_edge_types(conn)
        conn.close()
        return {
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "hebbian_count": hebbian_count,
            "backfill_count": backfill_count,
        }
    except Exception as e:
        return {"error": str(e), "hebbian_count": 0, "backfill_count": 0}


# ── Background worker ──────────────────────────────────────────────────────────


class EdgeMaintenanceWorker:
    """Runs consolidation on a background daemon thread."""

    def __init__(
        self, db_url: str | None = None, interval_s: float | None = None
    ) -> None:
        self._db_url = db_url
        self._interval_s = interval_s or _CONSOLIDATION_INTERVAL_S
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_run: dict | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="librarian-edge-maintenance", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.last_run = run_consolidation(self._db_url)
            log.info("edge_maintenance cycle: %s", self.last_run)
            self._stop.wait(timeout=self._interval_s)

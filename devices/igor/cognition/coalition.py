"""
coalition.py — Binding: coalition detection on hot node set.

After spreading activation produces a heat field, detect clusters of
mutually-reinforcing hot nodes (coalitions). A coalition = connected
subgraph of hot nodes whose combined activation exceeds a threshold.

This is the binding step: the top coalition IS what Igor is "about"
right now — a coherent percept from the graph's current activation state.

Phase 1 (this file): detect coalitions from heat field, log to ring.
Phase 2 (deferred): feed top coalition into NE frame selection + BG scoring.

Called from: NarrativeEngine._run() after _process_gaps().
Ref: T-binding, T-spreading-activation (prereq, done).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..igor_base import get_logger

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = get_logger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

COALITION_MIN_HEAT = 0.3  # Minimum heat for a node to join a coalition
COALITION_MIN_SIZE = 2  # Minimum nodes for a coalition (singletons excluded)
COALITION_MAX_NODES = 30  # Cap seed set to avoid O(N²) edge query


def detect_coalitions(
    cortex: "Cortex",
    heat_field: dict,
    min_heat: float = COALITION_MIN_HEAT,
    min_size: int = COALITION_MIN_SIZE,
) -> list[dict]:
    """
    Detect coalitions from a spreading-activation heat field.

    Args:
        cortex: Cortex instance (used to query interpretive_edges)
        heat_field: dict[node_id, float] from spreading_activation()
        min_heat: minimum heat threshold to be a "hot node"
        min_size: minimum coalition size (singletons excluded)

    Returns:
        List of coalition dicts, sorted by aggregate weight DESC:
            {
              "nodes": [node_id, ...],
              "weight": float,        # sum of member heats
              "centroid": node_id,    # highest-heat member
              "size": int,
            }
        Empty list on failure or if no coalitions found.
    """
    if not heat_field:
        return []

    # Get hot nodes (heat >= threshold), capped to avoid large queries
    hot = sorted(
        ((nid, h) for nid, h in heat_field.items() if h >= min_heat),
        key=lambda x: x[1],
        reverse=True,
    )[:COALITION_MAX_NODES]

    if len(hot) < min_size:
        return []

    hot_ids = [nid for nid, _ in hot]
    heat_by_id = dict(hot)

    # Fetch all edges between hot nodes in one query
    adjacency: dict[str, set[str]] = {nid: set() for nid in hot_ids}
    try:
        with cortex._conn() as conn:
            placeholders = ",".join(["%s"] * len(hot_ids))
            rows = conn.execute(
                f"""
                SELECT from_id, to_id FROM interpretive_edges
                WHERE from_id IN ({placeholders})
                  AND to_id IN ({placeholders})
                  AND direction != 'inhibition'
                """,
                hot_ids + hot_ids,
            ).fetchall()
        for row in rows:
            from_id, to_id = row[0], row[1]
            if from_id in adjacency and to_id in adjacency:
                adjacency[from_id].add(to_id)
                adjacency[to_id].add(from_id)  # undirected for clustering
    except Exception as e:
        logger.debug("detect_coalitions edge query failed: %s", e)
        return []

    # BFS to find connected clusters
    visited: set[str] = set()
    coalitions: list[dict] = []

    for start in hot_ids:
        if start in visited:
            continue
        cluster: list[str] = []
        queue = [start]
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            cluster.append(node)
            queue.extend(nb for nb in adjacency[node] if nb not in visited)

        if len(cluster) < min_size:
            continue

        weight = sum(heat_by_id.get(nid, 0.0) for nid in cluster)
        centroid = max(cluster, key=lambda nid: heat_by_id.get(nid, 0.0))
        coalitions.append(
            {
                "nodes": cluster,
                "weight": round(weight, 4),
                "centroid": centroid,
                "size": len(cluster),
            }
        )

    coalitions.sort(key=lambda c: c["weight"], reverse=True)
    return coalitions

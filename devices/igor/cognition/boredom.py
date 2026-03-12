"""
boredom.py — #178: Boredom monitor.

Tracks traversal frequency per memory node within a rolling time window.
When a node is traversed too often, its effective weight is penalized —
forcing exploration of less-used paths and wider vocabulary.

The "boredom" signal: when the same inference paths fire repeatedly,
resistance builds until a fresh path is found.

Gate: IGOR_BOREDOM_ENABLED (default false).
"""

import os
import time
from collections import defaultdict

# Rolling window in seconds (default 1 hour)
_WINDOW_SECS = int(os.getenv("IGOR_BOREDOM_WINDOW_SECS", "3600"))
# Traversals within window before penalty kicks in
_THRESHOLD    = int(os.getenv("IGOR_BOREDOM_THRESHOLD", "5"))
# Weight multiplier when bored (0.5 = half the normal weight)
_PENALTY      = float(os.getenv("IGOR_BOREDOM_PENALTY", "0.5"))

# node_id → list of timestamps (pruned to window on each access)
_traversal_log: dict[str, list[float]] = defaultdict(list)


def enabled() -> bool:
    return os.getenv("IGOR_BOREDOM_ENABLED", "false").lower() == "true"


def record_traversal(node_id: str) -> None:
    """Record that node_id was traversed right now."""
    if not enabled():
        return
    now = time.monotonic()
    _traversal_log[node_id].append(now)
    # Prune old entries outside the rolling window
    cutoff = now - _WINDOW_SECS
    _traversal_log[node_id] = [t for t in _traversal_log[node_id] if t >= cutoff]


def record_traversals(node_ids: list[str]) -> None:
    """Record traversal for a list of node IDs at once."""
    for nid in node_ids:
        record_traversal(nid)


def weight_modifier(node_id: str) -> float:
    """
    Return the weight modifier for node_id.
    1.0 = normal. _PENALTY (e.g. 0.5) = bored, apply resistance.
    """
    if not enabled():
        return 1.0
    now = time.monotonic()
    cutoff = now - _WINDOW_SECS
    recent = [t for t in _traversal_log.get(node_id, []) if t >= cutoff]
    if len(recent) >= _THRESHOLD:
        return _PENALTY
    return 1.0


def apply_boredom(memories: list, attr: str = "relevance_score") -> list:
    """
    Apply boredom weight modifier to a list of Memory objects in-place.
    Reduces relevance_score for over-traversed nodes.
    Returns the list sorted by adjusted score descending.
    """
    if not enabled():
        return memories
    for m in memories:
        mod = weight_modifier(m.id)
        if mod < 1.0:
            current = getattr(m, attr, 0.0) or 0.0
            setattr(m, attr, current * mod)
    memories.sort(key=lambda m: getattr(m, attr, 0.0) or 0.0, reverse=True)
    return memories


def boredom_level() -> float:
    """
    Return a 0.0–1.0 measure of current boredom.
    0.0 = fresh (everything novel). 1.0 = maximal repetition across all tracked nodes.
    """
    if not _traversal_log:
        return 0.0
    now = time.monotonic()
    cutoff = now - _WINDOW_SECS
    bored_count = sum(
        1 for ts in _traversal_log.values()
        if len([t for t in ts if t >= cutoff]) >= _THRESHOLD
    )
    return min(1.0, bored_count / max(1, len(_traversal_log)))


def reset(node_id: str | None = None) -> None:
    """Reset traversal log for a node, or all nodes if node_id is None."""
    if node_id is None:
        _traversal_log.clear()
    else:
        _traversal_log.pop(node_id, None)

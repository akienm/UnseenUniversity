"""
trail_inspector.py — T-trails-infra: Igor self-inspection of activation trails.

Registers `inspect_trail` so Igor can narrate his own recent reasoning paths,
and `trail_hot_paths` to surface the most frequently co-activated node pairs.
"""

import logging
from .registry import Tool, registry

logger = logging.getLogger("igor.tools.trail_inspector")


def inspect_trail(node_id: str = "", limit: str = "5") -> str:
    """
    Inspect recent activation trails.

    node_id — if given, show trails that passed through this node, plus its
              heat gradient (rising/flat/fading). Empty = show recent traces
              as a reasoning path narrative.
    limit   — max trails/traces to show (default 5).
    """
    import os
    from pathlib import Path
    from ..memory.cortex import Cortex

    try:
        _limit = max(1, min(20, int(limit)))
    except Exception:
        _limit = 5

    cortex = Cortex(None)

    lines = []

    if node_id.strip():
        nid = node_id.strip()
        # Heat + gradient for this node
        heat = cortex.get_tail_heat(nid)
        grad = cortex.trail_gradient(nid)
        trend_arrow = {"rising": "↑", "flat": "→", "fading": "↓"}.get(
            grad["trend"], "?"
        )
        lines.append(
            f"Trail heat for {nid}: {heat:.4f}  {trend_arrow} {grad['trend']}"
            f"  (recent={grad['recent_heat']:.4f}  earlier={grad['earlier_heat']:.4f})"
        )
        # Trails that passed through this node
        trails = cortex.trails_through_node(nid, limit=_limit)
        if not trails:
            lines.append("  No recent trails found for this node.")
        else:
            lines.append(f"  {len(trails)} recent trail(s) through {nid}:")
            for t in trails:
                path = " → ".join(n["node_id"] for n in t["nodes"])
                lines.append(f"  [{t['recorded_at'][:19]}] {path}")
    else:
        # Narrate recent search traces as reasoning paths
        traces = cortex.get_recent_traces(limit=_limit)
        if not traces:
            lines.append("No recent traces found.")
        else:
            lines.append(f"My {len(traces)} most recent reasoning path(s):")
            for tr in traces:
                q = tr.get("query", "")[:60]
                nodes = tr.get("nodes", [])
                path = " → ".join(
                    f"{n['node_id']}({n['relevance']:.2f})" for n in nodes[:6]
                )
                if len(nodes) > 6:
                    path += f" … (+{len(nodes)-6} more)"
                lines.append(f"  [{tr['recorded_at'][:19]}] query='{q}'")
                lines.append(f"    path: {path}")

    logger.info(
        "inspect_trail: node_id=%r limit=%d → %d lines", node_id, _limit, len(lines)
    )
    return "\n".join(lines) if lines else "No trail data available."


def trail_hot_paths(limit: str = "10", since_hours: str = "24") -> str:
    """
    Show the most frequently co-activated node pairs from recent trails.

    limit       — max pairs to return (default 10)
    since_hours — how far back to look (default 24h)
    """
    import os
    from pathlib import Path
    from ..memory.cortex import Cortex

    try:
        _limit = max(1, min(50, int(limit)))
        _hours = max(1, int(since_hours))
    except Exception:
        _limit, _hours = 10, 24

    cortex = Cortex(None)
    paths = cortex.hot_paths(limit=_limit, since_hours=_hours)

    if not paths:
        return f"No co-activated pairs found in the last {_hours}h."

    lines = [f"Hot paths (last {_hours}h, top {len(paths)}):"]
    for p in paths:
        lines.append(
            f"  {p['node_a']} ↔ {p['node_b']}  ×{p['co_count']}  last={p['last_seen'][:19]}"
        )
    logger.info("trail_hot_paths: %d pairs returned", len(paths))
    return "\n".join(lines)


registry.register(
    Tool(
        name="inspect_trail",
        description=(
            "Inspect activation trails — Igor's own reasoning paths through memory. "
            "node_id='' → show recent search traces as reasoning narratives. "
            "node_id='X' → show heat, gradient (rising/flat/fading), and trails through X. "
            "Use this to explain 'how I got to that answer' or to debug what fired."
        ),
        parameters={
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "Memory node ID to inspect trails for (empty = recent traces)",
                },
                "limit": {
                    "type": "string",
                    "description": "Max trails/traces to return (default '5')",
                },
            },
            "required": [],
        },
        fn=inspect_trail,
    )
)

registry.register(
    Tool(
        name="trail_hot_paths",
        description=(
            "Show the most frequently co-activated node pairs from recent trails. "
            "Reveals which concepts consistently activate together — the matrix's hot edges."
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "string",
                    "description": "Max pairs to return (default '10')",
                },
                "since_hours": {
                    "type": "string",
                    "description": "Look-back window in hours (default '24')",
                },
            },
            "required": [],
        },
        fn=trail_hot_paths,
    )
)

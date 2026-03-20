"""
Graph operations tool — T-graph-calving.

Exposes cortex attractor detection, node adoption, and calving candidate scan
as registered tools callable by habits.

Gates:
  IGOR_NODE_ADOPTION_ENABLED=true  — enables adopt_orphans()
  IGOR_CALVING_ENABLED=true        — enables calving candidate scan (future)
"""

import os
import logging

from .registry import Tool, registry

logger = logging.getLogger("igor.tools.graph_ops")


def get_hot_attractors(limit: str = "10") -> str:
    """
    Return the top N attractor nodes — highest activation × inbound-edge score.
    Attractors are the emergent semantic centres of gravity in the memory graph.
    """
    n = int(limit) if str(limit).isdigit() else 10
    try:
        from ..memory.cortex import Cortex
        from ..memory.db_proxy import make_home_proxy
        from ..paths import paths

        cortex = Cortex(paths().instance / "wild-0001.db")
        attractors = cortex.get_attractors(limit=n)
        if not attractors:
            return "No attractors found yet — graph may be too sparse."
        lines = [f"Top {len(attractors)} attractors:"]
        for a in attractors:
            lines.append(
                f"  [{a.memory_type}] {a.id[:8]}… "
                f"act={a.activation_count}  {a.narrative[:60]}"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.error("get_hot_attractors failed: %s", e)
        return f"Error: {e}"


def run_node_adoption(batch_size: str = "50") -> str:
    """
    Adopt orphan nodes into the nearest attractor tree via embedding similarity.
    Each orphan gets one interpretive_edge(direction='adoption') to its best-match attractor.
    Gate: IGOR_NODE_ADOPTION_ENABLED=true.
    """
    n = int(batch_size) if str(batch_size).isdigit() else 50
    try:
        from ..memory.cortex import Cortex
        from ..paths import paths

        cortex = Cortex(paths().instance / "wild-0001.db")
        adopted = cortex.adopt_orphans(batch_size=n)
        if adopted == 0:
            gate = os.getenv("IGOR_NODE_ADOPTION_ENABLED", "false")
            if gate.lower() != "true":
                return (
                    "Node adoption is gated off (IGOR_NODE_ADOPTION_ENABLED != true)."
                )
            return "No adoptable orphans found in this batch."
        logger.info("graph_ops: adopted %d orphan nodes", adopted)
        return f"Adopted {adopted} orphan nodes into attractor trees."
    except Exception as e:
        logger.error("run_node_adoption failed: %s", e)
        return f"Error: {e}"


def run_calving_check(depth_threshold: str = "5") -> str:
    """
    Scan for nodes deeper than depth_threshold hops from root.
    Reports candidates — does not calve automatically yet.
    Gate: IGOR_CALVING_ENABLED=true.
    """
    if os.getenv("IGOR_CALVING_ENABLED", "false").lower() != "true":
        return "Calving is gated off (IGOR_CALVING_ENABLED != true)."
    threshold = int(depth_threshold) if str(depth_threshold).isdigit() else 5
    try:
        from ..memory.cortex import Cortex
        from ..paths import paths

        cortex = Cortex(paths().instance / "wild-0001.db")
        candidates = cortex.find_calving_candidates(depth_threshold=threshold)
        if not candidates:
            return f"No calving candidates found (max tree depth < {threshold})."
        logger.info(
            "graph_ops: %d calving candidates at depth>%d", len(candidates), threshold
        )
        return (
            f"{len(candidates)} calving candidates at depth>{threshold}. "
            f"First 5: {candidates[:5]}"
        )
    except Exception as e:
        logger.error("run_calving_check failed: %s", e)
        return f"Error: {e}"


registry.register(
    Tool(
        name="get_hot_attractors",
        description=(
            "Return the top N attractor nodes in the memory graph — "
            "highest activation × inbound-edge score. "
            "Optional: limit (default 10)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "string",
                    "description": "Number of attractors (default 10)",
                }
            },
            "required": [],
        },
        fn=get_hot_attractors,
    )
)

registry.register(
    Tool(
        name="run_node_adoption",
        description=(
            "Adopt orphan memory nodes into the nearest attractor tree via embedding similarity. "
            "Requires IGOR_NODE_ADOPTION_ENABLED=true. "
            "Optional: batch_size (default 50)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "batch_size": {
                    "type": "string",
                    "description": "Nodes per batch (default 50)",
                }
            },
            "required": [],
        },
        fn=run_node_adoption,
    )
)

registry.register(
    Tool(
        name="run_calving_check",
        description=(
            "Scan for memory nodes deeper than depth_threshold from root — calving candidates. "
            "Requires IGOR_CALVING_ENABLED=true. "
            "Optional: depth_threshold (default 5)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "depth_threshold": {
                    "type": "string",
                    "description": "Hop depth threshold (default 5)",
                }
            },
            "required": [],
        },
        fn=run_calving_check,
    )
)

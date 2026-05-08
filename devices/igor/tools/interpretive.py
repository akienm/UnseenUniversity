"""
Interpretive tree tools — G52.

Igor can add traversal edges between memories and traverse the interpretive
tree from a set of seed nodes. These tools let the interpretive layer grow
organically through interactions rather than only through seed scripts.

CP1-CP6 are the root nodes. Edges have 4-part semantics:
  direction: activation | inhibition
  condition_csb: when this edge fires
  meaning_payload: what reaching the target means about self/situation
  action_pointer: next tree to explore after this meaning activates
"""

import json
import os
from pathlib import Path

from lab.utility_closet.registry import Tool, registry


def _get_cortex():
    from ..memory.cortex import Cortex

    return Cortex(None)


def add_interpretive_edge(
    from_id: str,
    to_id: str,
    direction: str = "activation",
    condition_csb: str = "",
    meaning_payload: str = "",
    action_pointer: str = "",
    weight: float = 1.0,
    **_,
) -> str:
    """
    Add a directed edge in the interpretive tree between two memory nodes.

    from_id: source memory id (often a CP or INTERPRETIVE memory)
    to_id: target memory id
    direction: "activation" (traversal follows this edge) or "inhibition" (suppresses target)
    condition_csb: CSB context string specifying when this edge fires; empty = always
    meaning_payload: what reaching to_id means about self or situation
    action_pointer: memory id or code_ref of the next action tree to explore
    weight: edge strength [0.0, 1.0]
    """
    try:
        cortex = _get_cortex()
        # Verify both memories exist
        src = cortex.get(from_id)
        tgt = cortex.get(to_id)
        if not src:
            return f"Error: from_id '{from_id}' not found in cortex."
        if not tgt:
            return f"Error: to_id '{to_id}' not found in cortex."
        edge_id = cortex.add_interpretive_edge(
            from_id=from_id,
            to_id=to_id,
            direction=direction,
            condition_csb=condition_csb,
            meaning_payload=meaning_payload,
            action_pointer=action_pointer,
            weight=float(weight),
        )
        return (
            f"Interpretive edge {edge_id} added: {from_id} "
            f"{'──►' if direction == 'activation' else '─╌►'} {to_id}  "
            f"(weight={weight:.2f})"
        )
    except Exception as e:
        return f"Error adding interpretive edge: {e}"


def interpretive_traverse(
    from_ids: str,
    max_depth: int = 3,
    min_weight: float = 0.1,
    **_,
) -> str:
    """
    Traverse the interpretive tree from seed node ids.

    Returns the INTERPRETIVE memories reachable from the seed nodes via
    activation edges, in traversal order (breadth-first).

    from_ids: comma-separated list of memory ids to start from
    max_depth: maximum traversal depth (default 3)
    min_weight: skip edges below this weight (default 0.1)
    """
    try:
        seed_list = [x.strip() for x in from_ids.split(",") if x.strip()]
        if not seed_list:
            return "Error: from_ids must be a comma-separated list of memory ids."
        cortex = _get_cortex()
        memories = cortex.interpretive_traverse(
            from_ids=seed_list,
            max_depth=int(max_depth),
            min_weight=float(min_weight),
        )
        if not memories:
            return "No interpretive memories reachable from those nodes."
        lines = [
            f"Interpretive traverse from [{', '.join(seed_list)}]:",
            f"  depth={max_depth}  min_weight={min_weight}  found={len(memories)}",
            "",
        ]
        for i, mem in enumerate(memories, 1):
            lines.append(f"  {i}. [{mem.id}] {mem.narrative[:120]}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error traversing interpretive tree: {e}"


def get_interpretive_edges(from_id: str, **_) -> str:
    """
    List all outgoing interpretive edges from a memory node.

    from_id: memory id to get edges from
    """
    try:
        cortex = _get_cortex()
        edges = cortex.get_interpretive_edges(from_id)
        if not edges:
            return f"No interpretive edges from '{from_id}'."
        lines = [f"Interpretive edges from {from_id} ({len(edges)} total):"]
        for e in edges:
            arrow = "──►" if e["direction"] == "activation" else "─╌►"
            payload_preview = (
                e["meaning_payload"][:60] if e["meaning_payload"] else "(none)"
            )
            lines.append(
                f"  [{e['id']}] {arrow} {e['to_id']}"
                f"  w={e['weight']:.2f}  {payload_preview}"
            )
            if e["condition_csb"]:
                lines.append(f"       condition: {e['condition_csb'][:80]}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading interpretive edges: {e}"


registry.register(
    Tool(
        name="add_interpretive_edge",
        description=(
            "G52: Add a directed edge in the interpretive tree between two memory nodes. "
            "CP1-CP6 are root nodes. direction: 'activation' or 'inhibition'. "
            "Use this to grow the interpretive layer when a new meaning connection is discovered."
        ),
        parameters={
            "type": "object",
            "properties": {
                "from_id": {
                    "type": "string",
                    "description": "Source memory id (often CP1-CP6 or an INTERPRETIVE memory)",
                },
                "to_id": {"type": "string", "description": "Target memory id"},
                "direction": {
                    "type": "string",
                    "enum": ["activation", "inhibition"],
                    "description": "Edge type",
                },
                "condition_csb": {
                    "type": "string",
                    "description": "CSB string: when this edge fires; empty = always",
                },
                "meaning_payload": {
                    "type": "string",
                    "description": "What reaching to_id means about self/situation",
                },
                "action_pointer": {
                    "type": "string",
                    "description": "Memory id or code_ref of next action tree to explore",
                },
                "weight": {
                    "type": "number",
                    "description": "Edge strength [0.0, 1.0]",
                    "default": 1.0,
                },
            },
            "required": ["from_id", "to_id"],
        },
        fn=add_interpretive_edge,
    )
)

registry.register(
    Tool(
        name="interpretive_traverse",
        description=(
            "G52: Traverse the interpretive tree from seed memory nodes. "
            "Returns INTERPRETIVE memories reachable via activation edges in breadth-first order. "
            "Use to find meaning schemas relevant to the current context."
        ),
        parameters={
            "type": "object",
            "properties": {
                "from_ids": {
                    "type": "string",
                    "description": "Comma-separated list of memory ids to start from",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum traversal depth (default 3)",
                    "default": 3,
                },
                "min_weight": {
                    "type": "number",
                    "description": "Skip edges below this weight (default 0.1)",
                    "default": 0.1,
                },
            },
            "required": ["from_ids"],
        },
        fn=interpretive_traverse,
    )
)

registry.register(
    Tool(
        name="get_interpretive_edges",
        description=(
            "G52: List all outgoing interpretive edges from a memory node. "
            "Shows direction, target, weight, meaning_payload, and condition."
        ),
        parameters={
            "type": "object",
            "properties": {
                "from_id": {
                    "type": "string",
                    "description": "Memory id to get edges from",
                },
            },
            "required": ["from_id"],
        },
        fn=get_interpretive_edges,
    )
)

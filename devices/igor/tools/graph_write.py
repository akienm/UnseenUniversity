"""
graph_write.py — In-turn graph-write tools (T-inline-graph-write).

Igor can call these tools during the think phase to deposit memories,
link nodes, and trigger embedding — without CC bridge intervention.

Tools:
  store_memory   — create and persist a new memory node
  link_memory    — add a parent→child edge between two existing nodes
  embed_node     — trigger embedding computation for a node (enables cosine search)

Safety:
  - All tools return error strings on failure, never raise.
  - store_memory is rate-gated: IGOR_GRAPH_WRITE_LIMIT per-process (default 5).
    Prevents runaway tool-call loops from flooding the DB.
  - memory_type is validated against the MemoryType enum before any DB write.
  - source is always "self_edit" so provenance is traceable.
"""

import os
import threading
from datetime import datetime
from pathlib import Path

from .registry import Tool, registry

# ── Per-process write rate gate ───────────────────────────────────────────────

_write_lock = threading.Lock()
_write_count = 0
_WRITE_LIMIT = int(os.getenv("IGOR_GRAPH_WRITE_LIMIT", "5"))

_VALID_TYPES = frozenset(
    {
        "ROOT",
        "CORE_PATTERN",
        "IDENTITY",
        "ROLE_MODEL",
        "EPISODIC",
        "PROCEDURAL",
        "INTERPRETIVE",
        "EXPERIENTIAL",
        "FACTUAL",
        "REFERENCE",
    }
)


def _get_cortex():
    """Open a Cortex instance routed to the home DB."""
    from ..memory.cortex import Cortex as _Cortex

    return _Cortex(None)


# ── store_memory ──────────────────────────────────────────────────────────────


def store_memory(
    narrative: str,
    memory_type: str,
    parent_id: str = "",
    context: str = "",
    valence: str = "0.0",
    arousal: str = "0.0",
) -> str:
    """
    Create and persist a new memory node in Igor's graph during a live turn.

    narrative:    the content of the memory (required)
    memory_type:  one of INTERPRETIVE | FACTUAL | EXPERIENTIAL | EPISODIC |
                  PROCEDURAL | CORE_PATTERN | IDENTITY | ROLE_MODEL |
                  REFERENCE | ROOT
    parent_id:    optional — if set, attaches this node as a child of parent
    context:      brief description of why this memory is being deposited now
    valence:      emotional valence [-1.0, 1.0] (default 0.0)
    arousal:      emotional arousal [-1.0, 1.0] (default 0.0)

    Returns the new memory ID (8-char) so it can be passed to embed_node.
    """
    global _write_count
    with _write_lock:
        if _write_count >= _WRITE_LIMIT:
            return (
                f"[store_memory BLOCKED] write limit ({_WRITE_LIMIT}) reached this turn. "
                "Increase IGOR_GRAPH_WRITE_LIMIT or defer to next turn."
            )
        _write_count += 1

    mt_upper = memory_type.strip().upper()
    if mt_upper not in _VALID_TYPES:
        return (
            f"[store_memory ERROR] unknown memory_type '{memory_type}'. "
            f"Valid: {', '.join(sorted(_VALID_TYPES))}"
        )

    try:
        v = float(valence)
        a = float(arousal)
    except (TypeError, ValueError):
        return f"[store_memory ERROR] valence and arousal must be numbers, got '{valence}', '{arousal}'"

    try:
        from ..memory.models import Memory as _Mem, MemoryType as _MT

        mem = _Mem(
            narrative=narrative,
            memory_type=_MT[mt_upper],
            parent_id=parent_id or None,
            valence=v,
            arousal=a,
            source="self_edit",
            context_of_encoding=context
            or f"deposited during turn {datetime.now().strftime('%Y-%m-%dT%H:%M')}",
            metadata={"turn_deposited": True},
        )
        cortex = _get_cortex()
        cortex.store(mem)
        if parent_id:
            cortex.add_child(parent_id, mem.id)
        return f"stored {mem.id}: {narrative[:80]}"
    except Exception as e:
        return f"[store_memory ERROR] {e}"


# ── link_memory ───────────────────────────────────────────────────────────────


def link_memory(parent_id: str, child_id: str) -> str:
    """
    Add a parent→child edge between two existing memory nodes.

    parent_id:  ID of the parent node (e.g. "CP1", "CP2", or any 8-char ID)
    child_id:   ID of the child node to attach

    Use this after store_memory to wire a new node into the graph, or to
    connect two pre-existing nodes.
    """
    if not parent_id or not child_id:
        return "[link_memory ERROR] both parent_id and child_id are required"
    try:
        cortex = _get_cortex()
        parent = cortex.get(parent_id)
        if parent is None:
            return f"[link_memory ERROR] parent_id '{parent_id}' not found"
        child = cortex.get(child_id)
        if child is None:
            return f"[link_memory ERROR] child_id '{child_id}' not found"
        cortex.add_child(parent_id, child_id)
        return f"linked {child_id} → {parent_id}"
    except Exception as e:
        return f"[link_memory ERROR] {e}"


# ── embed_node ────────────────────────────────────────────────────────────────


def embed_node(memory_id: str) -> str:
    """
    Trigger embedding computation for a memory node.

    After store_memory, Phase 2 cosine search won't find the node until it
    has an embedding vector. Call embed_node(memory_id) to compute and store
    the embedding immediately. If Ollama is unavailable, the node will be
    embedded on next startup — this call is a best-effort accelerator, not
    required for correctness.

    memory_id:  8-char memory ID returned by store_memory
    """
    if not memory_id:
        return "[embed_node ERROR] memory_id is required"
    try:
        cortex = _get_cortex()
        mem = cortex.get(memory_id)
        if mem is None:
            return f"[embed_node ERROR] memory_id '{memory_id}' not found"
        vec = cortex._get_or_compute_embedding(mem)
        if vec is None:
            return f"embed skipped: embedder unavailable — {memory_id} will embed on next startup"
        return f"embedded {memory_id} ({len(vec)}-dim)"
    except Exception as e:
        return f"[embed_node ERROR] {e}"


# ── Registration ──────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="store_memory",
        description=(
            "Create and persist a new memory node in Igor's graph. "
            "Use memory_type=INTERPRETIVE to deposit a realization or meaning-making node, "
            "FACTUAL for a fact, EXPERIENTIAL for an experience. "
            "Returns the new memory ID — pass it to embed_node to make it searchable."
        ),
        parameters={
            "type": "object",
            "properties": {
                "narrative": {
                    "type": "string",
                    "description": "The content of the memory.",
                },
                "memory_type": {
                    "type": "string",
                    "enum": sorted(_VALID_TYPES),
                    "description": "Memory type. INTERPRETIVE for realizations; FACTUAL for facts; EXPERIENTIAL for experiences.",
                },
                "parent_id": {
                    "type": "string",
                    "description": "Optional parent node ID (e.g. 'CP1', 'CP2'). Attaches this node as a child.",
                },
                "context": {
                    "type": "string",
                    "description": "Brief description of why this memory is being deposited now.",
                },
                "valence": {
                    "type": "string",
                    "description": "Emotional valence [-1.0, 1.0]. Default 0.0.",
                },
                "arousal": {
                    "type": "string",
                    "description": "Emotional arousal [-1.0, 1.0]. Default 0.0.",
                },
            },
            "required": ["narrative", "memory_type"],
        },
        fn=store_memory,
    )
)

registry.register(
    Tool(
        name="link_memory",
        description=(
            "Add a parent→child edge between two existing memory nodes. "
            "Use after store_memory to wire a new node into the graph hierarchy, "
            "or to connect any two pre-existing nodes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "parent_id": {
                    "type": "string",
                    "description": "ID of the parent node (e.g. 'CP1', 'CP2', or any 8-char ID).",
                },
                "child_id": {
                    "type": "string",
                    "description": "ID of the child node to attach.",
                },
            },
            "required": ["parent_id", "child_id"],
        },
        fn=link_memory,
    )
)

registry.register(
    Tool(
        name="embed_node",
        description=(
            "Trigger embedding computation for a memory node so it becomes findable "
            "via cosine search. Call this after store_memory with the returned memory ID. "
            "Safe to call even if Ollama is unavailable — falls back gracefully."
        ),
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "The 8-char memory ID returned by store_memory.",
                },
            },
            "required": ["memory_id"],
        },
        fn=embed_node,
    )
)

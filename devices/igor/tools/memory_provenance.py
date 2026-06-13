"""
memory_provenance.py — T-memory-provenance

Memories created by inference (NE synthesis, tool output, upstream injection,
LLM deposits) carry a claim about reality. The claim is only as reliable as
the reasoning that made it.

This module provides tools to:
- validate_memory(id)         — mark a memory as confirmed/observed
- flag_memory_provenance(id)  — tag with provenance_source + reasoning_ref
- list_unvalidated_memories() — surface memories needing validation

Design principle (per Akien, 2026-04-04): not about rejecting injected
memories — about knowing their epistemic status. Unvalidated memories are
usable but should not be treated as ground truth.

validation_status values:
  unvalidated  — created by inference; not yet confirmed
  validated    — confirmed by Akien or directly observed
  rejected     — found to be false/stale; should not be relied upon
"""

import logging
from datetime import datetime

from devices.igor.tools.registry import Tool, registry

logger = logging.getLogger(__name__)


def validate_memory(memory_id: str, **_) -> str:
    """Mark a memory as validated (confirmed by Akien or directly observed)."""
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex()
        mem = cortex.get(memory_id)
        if mem is None:
            return f"validate_memory: memory {memory_id!r} not found"
        mem.metadata["validation_status"] = "validated"
        mem.metadata["validated_at"] = datetime.now().isoformat()
        cortex.store(mem)
        return f"validate_memory: {memory_id} marked validated"
    except Exception as e:
        logger.warning("validate_memory failed %s: %s", memory_id, e)
        return f"validate_memory: error — {e}"


def reject_memory(memory_id: str, reason: str = "", **_) -> str:
    """Mark a memory as rejected (found to be false or stale)."""
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex()
        mem = cortex.get(memory_id)
        if mem is None:
            return f"reject_memory: memory {memory_id!r} not found"
        mem.metadata["validation_status"] = "rejected"
        mem.metadata["rejected_at"] = datetime.now().isoformat()
        if reason:
            mem.metadata["rejection_reason"] = reason[:200]
        cortex.store(mem)
        return f"reject_memory: {memory_id} marked rejected"
    except Exception as e:
        logger.warning("reject_memory failed %s: %s", memory_id, e)
        return f"reject_memory: error — {e}"


def list_unvalidated_memories(limit: int = 10, **_) -> str:
    """List recent memories with validation_status=unvalidated."""
    try:
        from ..memory.cortex import Cortex as _Cortex
        import psycopg2
        import os

        db_url = os.environ.get("UU_HOME_DB_URL", "")
        if not db_url:
            return "list_unvalidated_memories: UU_HOME_DB_URL not set"

        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, memory_type, narrative, metadata->>'provenance_source', timestamp
            FROM memories
            WHERE jsonb_exists(metadata, 'validation_status')
              AND metadata->>'validation_status' = 'unvalidated'
            ORDER BY timestamp DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return "list_unvalidated_memories: none found"

        lines = [f"UNVALIDATED MEMORIES ({len(rows)}):"]
        for mem_id, mem_type, narrative, prov_src, ts in rows:
            ts_str = ts.strftime("%Y-%m-%dT%H:%M") if ts else "?"
            src = prov_src or "unknown"
            lines.append(
                f"  [{ts_str}] {mem_id} ({mem_type}) source={src}\n"
                f"    {narrative[:120] if narrative else ''}"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("list_unvalidated_memories failed: %s", e)
        return f"list_unvalidated_memories: error — {e}"


registry.register(
    Tool(
        name="validate_memory",
        description=(
            "Mark a memory as validated — confirmed by Akien or directly observed. "
            "Use when Akien says 'yes that's right', 'confirmed', or explicitly verifies a fact. "
            "Takes memory_id (string)."
        ),
        parameters={
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"],
        },
        fn=validate_memory,
    )
)

registry.register(
    Tool(
        name="reject_memory",
        description=(
            "Mark a memory as rejected — found to be false, stale, or injected incorrectly. "
            "Takes memory_id (string) and optional reason (string)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["memory_id"],
        },
        fn=reject_memory,
    )
)

registry.register(
    Tool(
        name="list_unvalidated_memories",
        description=(
            "List recent memories tagged as unvalidated — created by inference, "
            "NE synthesis, or upstream injection but not yet confirmed. "
            "Use to surface what needs epistemic review."
        ),
        parameters={
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "required": [],
        },
        fn=list_unvalidated_memories,
    )
)

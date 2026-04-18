"""
versioning.py — T-versioned-memories

Version control for memory nodes. When a versioned memory is updated,
the old state is preserved as a child node. All existing links continue
to point to the same node ID (the latest version).

Design:
  - Per-memory `versioned: true` flag in metadata
  - On update: copy current state as child, then update current node
  - Version child carries: version_of, version_ts, version_seq in metadata
  - History = children with version_of == parent.id, sorted by version_ts

Usage:
    from wild_igor.igor.memory.versioning import version_before_update

    # In cortex.store(), before the INSERT OR REPLACE:
    if memory.metadata.get("versioned"):
        version_before_update(cortex, memory)
"""

import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


def version_before_update(cortex, memory) -> Optional[str]:
    """
    If the memory already exists and is versioned, snapshot the current
    state as a child node before the update overwrites it.

    Returns the version node ID if a snapshot was created, None otherwise.
    """
    if not memory.metadata.get("versioned"):
        return None

    # Check if the memory already exists
    try:
        existing = cortex.get(memory.id)
    except Exception:
        return None

    if existing is None:
        # First store — no previous version to snapshot
        return None

    # Don't version if content hasn't actually changed
    if existing.narrative == memory.narrative and existing.metadata == memory.metadata:
        return None

    # Create version snapshot as child
    version_seq = _get_next_seq(cortex, memory.id)
    version_id = f"{memory.id}_v{version_seq:03d}"
    version_ts = datetime.now().isoformat()

    try:
        from .models import Memory, MemoryType

        version_meta = dict(existing.metadata) if existing.metadata else {}
        version_meta["version_of"] = memory.id
        version_meta["version_ts"] = version_ts
        version_meta["version_seq"] = version_seq
        # Remove the versioned flag from the snapshot — it's not itself versioned
        version_meta.pop("versioned", None)

        version_node = Memory(
            id=version_id,
            narrative=existing.narrative,
            memory_type=existing.memory_type,
            parent_id=memory.id,  # child of the current node
            valence=existing.valence,
            arousal=existing.arousal,
            dominance=existing.dominance,
            source="version_snapshot",
            confidence=existing.confidence,
            context_of_encoding=f"version|{memory.id}|seq={version_seq}",
            metadata=version_meta,
            payload=existing.payload,
            scope=existing.scope,
        )

        # Store directly — bypass versioning check for the snapshot itself
        cortex.store(version_node)
        log.debug("Versioned %s → %s (seq=%d)", memory.id, version_id, version_seq)
        return version_id

    except Exception as exc:
        log.warning("versioning failed for %s: %s", memory.id, exc)
        return None


def _get_next_seq(cortex, memory_id: str) -> int:
    """Get the next version sequence number for a memory."""
    try:
        with cortex._conn() as conn:
            row = conn.execute(
                "SELECT MAX((metadata->>'version_seq')::int) FROM memories "
                "WHERE metadata->>'version_of' = ?",
                (memory_id,),
            ).fetchone()
            current_max = row[0] if row and row[0] is not None else 0
            return current_max + 1
    except Exception:
        return 1


def get_version_history(cortex, memory_id: str) -> list[dict]:
    """Get version history for a memory, newest first."""
    try:
        with cortex._conn() as conn:
            rows = conn.execute(
                "SELECT id, narrative, metadata, timestamp FROM memories "
                "WHERE metadata->>'version_of' = ? "
                "ORDER BY (metadata->>'version_seq')::int DESC",
                (memory_id,),
            ).fetchall()
            return [
                {
                    "version_id": r["id"],
                    "narrative": r["narrative"][:200],
                    "version_seq": json.loads(
                        r["metadata"]
                        if isinstance(r["metadata"], str)
                        else json.dumps(r["metadata"])
                    ).get("version_seq", 0),
                    "version_ts": json.loads(
                        r["metadata"]
                        if isinstance(r["metadata"], str)
                        else json.dumps(r["metadata"])
                    ).get("version_ts", ""),
                    "timestamp": r["timestamp"],
                }
                for r in rows
            ]
    except Exception as exc:
        log.warning("get_version_history failed for %s: %s", memory_id, exc)
        return []

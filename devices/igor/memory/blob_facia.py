"""
blob_facia.py — T-blob-facia-and-tree-index (#443)

Each blob gets a facia memory (navigable entry-point) and a graph tree
registered in the tree index. The facia makes blobs discoverable via
cortex.search; the tree makes them traversable.

Call ensure_blob_facia(cortex, memory_id) after storing a blob to
create the facia + tree if they don't already exist.

Inertia: LOW (new module, doesn't touch cortex.py internals)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .cortex import Cortex

logger = logging.getLogger(__name__)

BLOB_TREE_PREFIX = "blob_"


def ensure_blob_facia(
    cortex: "Cortex",
    memory_id: str,
    *,
    display_name: str = "",
    tags: Optional[list[str]] = None,
) -> Optional[str]:
    """Create a facia + tree for a blob memory if they don't already exist.

    Returns the facia memory ID, or None on failure.
    The facia is a lightweight FACTUAL memory with facia_role=blob_index
    that points at the REFERENCE memory holding the actual blob.
    """
    try:
        mem = cortex.get(memory_id)
        if mem is None:
            logger.debug("blob_facia: memory %s not found", memory_id)
            return None

        if (mem.metadata or {}).get("blob_facia_id"):
            return mem.metadata["blob_facia_id"]

        from .models import Memory, MemoryType

        facia_name = display_name or f"Blob: {mem.narrative[:80]}"
        tag_list = tags or (mem.metadata or {}).get("tags", [])

        facia = Memory(
            narrative=facia_name,
            memory_type=MemoryType.FACTUAL,
            parent_id=memory_id,
            metadata={
                "facia_role": "blob_index",
                "display_name": facia_name,
                "blob_memory_id": memory_id,
                "blob_tags": tag_list,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        cortex.store(facia)

        cortex.add_interpretive_edge(
            from_id=facia.id,
            to_id=memory_id,
            direction="contains",
            weight=0.8,
            layer="blob_index",
        )

        try:
            from .tree_index import TreeIndex

            idx = TreeIndex()
            tree_name = f"{BLOB_TREE_PREFIX}{memory_id[:20]}"
            idx.create(
                name=tree_name,
                facia_id=facia.id,
                description=f"Blob tree for {facia_name[:60]}",
            )
        except Exception as exc:
            logger.debug("blob_facia: tree creation failed: %s", exc)

        try:
            _update_metadata(cortex, memory_id, "blob_facia_id", facia.id)
        except Exception:
            pass

        logger.info("[BLOB_FACIA] created facia %s for blob %s", facia.id, memory_id)
        return facia.id

    except Exception as exc:
        logger.warning("blob_facia: failed for %s: %s", memory_id, exc)
        return None


def _update_metadata(cortex: "Cortex", memory_id: str, key: str, value: str) -> None:
    """Set a single metadata key on an existing memory."""
    with cortex._db() as conn:
        conn.execute(
            "UPDATE memories SET metadata = jsonb_set("
            "COALESCE(metadata, '{}'::jsonb), %s, %s) WHERE id = %s",
            ([key], f'"{value}"', memory_id),
        )

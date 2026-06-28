"""
graph_integrator.py — T-graph-integrator: co-occurrence edges from indexed content (D230/D231)

Processes FACT_CLOUD nodes from a content_id and builds co-occurrence edges.

Steps per content_id:
1. Fetch all FACT_CLOUD nodes for this content_id
2. For each pair of nodes in same content:
   - Same chapter: edge weight += 0.2 (strong proximity)
   - Adjacent chapters: edge weight += 0.1
   - Distant chapters: edge weight += 0.05
   - Cap weight at 1.0
3. Create interpretive edges with relation=co_deposited
4. Create ROOT anchor node (BOOK_{content_id_short}) with edges to all FACT_CLOUD nodes
5. Queue T-self-test with content_id

Used by:
  - Prospective: called after T-reading-indexer completes for a content_id
  - Retrospective: called by quiet-period replay habit (T-bio-replay)

Usage:
  from unseen_university.devices.igor.cognition.graph_integrator import integrate_graph
  content_id = "<uuid>"
  integrate_graph(content_id)  # → creates co-occurrence edges + ROOT node
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from ..memory.cortex import Cortex
from ..memory.models import Memory, MemoryType
from ..igor_base import get_logger

logger = get_logger(__name__)


def _get_cortex() -> Optional[Cortex]:
    """Get cortex for current instance."""
    try:
        return Cortex(None)
    except Exception as e:
        logger.error(f"Failed to get cortex: {e}")
        return None


def _fetch_fact_clouds(cortex: Cortex, content_id: str) -> list[dict]:
    """
    Fetch all FACT_CLOUD nodes for a content_id.

    Returns list of dicts: {id, narrative, chapter_idx, chunk_idx, confidence}
    """
    if not cortex:
        return []

    # Search for all FACTUAL memories starting with FACT_CLOUD_
    # Then filter by content_id in the metadata
    with cortex._conn() as conn:
        rows = conn.execute(
            """
            SELECT id, narrative, metadata
            FROM memories
            WHERE memory_type = 'FACTUAL'
            AND id LIKE %s
            ORDER BY id
            """,
            ("FACT_CLOUD_%",),
        ).fetchall()

    fact_clouds = []
    for row in rows:
        mem_id = row[0]
        narrative = row[1]
        try:
            raw_meta = row[2] or {}
            metadata = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)
            chapter_idx = metadata.get("chapter_idx", -1)
            chunk_idx = metadata.get("chunk_idx", -1)
            confidence = metadata.get("extraction_confidence", 0.6)

            # Only include if from this content_id
            if metadata.get("content_id") == content_id:
                fact_clouds.append(
                    {
                        "id": mem_id,
                        "narrative": narrative,
                        "chapter_idx": chapter_idx,
                        "chunk_idx": chunk_idx,
                        "confidence": confidence,
                    }
                )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse metadata for {mem_id}: {e}")

    return fact_clouds


def _calculate_edge_weight(chapter_a: int, chapter_b: int) -> float:
    """Calculate edge weight based on chapter proximity."""
    if chapter_a == chapter_b:
        # Same chapter: strong proximity
        return 0.2

    chapter_diff = abs(chapter_a - chapter_b)
    if chapter_diff == 1:
        # Adjacent chapters
        return 0.1
    else:
        # Distant chapters
        return 0.05


def _create_book_anchor_node(
    cortex: Cortex,
    content_id: str,
    title: str,
    author: str,
) -> str:
    """
    Create a ROOT anchor node for this book.

    Node ID: BOOK_{content_id_short}
    Returns the node ID.
    """
    # Generate short content_id (first 8 chars)
    content_id_short = content_id[:8].upper()
    node_id = f"BOOK_{content_id_short}"

    # Check if an anchor for this content_id already exists.
    # Use metadata lookup, not ID — post-D256 migration renamed BOOK_xxx IDs
    # to timestamp IDs, so ID-based lookup would miss the existing node.
    with cortex._conn() as conn:
        existing = conn.execute(
            "SELECT id FROM memories "
            "WHERE metadata->>'content_id' = %s "
            "AND metadata->>'is_book_anchor' = 'true' LIMIT 1",
            (content_id,),
        ).fetchone()

    if existing:
        existing_id = existing[0]
        logger.info(
            f"ROOT anchor for content_id={content_id!r} already exists: {existing_id}"
        )
        return existing_id

    # Create the anchor node
    narrative = f"Book: {title}"
    if author:
        narrative += f" by {author}"

    metadata = {
        "content_id": content_id,
        "is_book_anchor": True,
    }

    mem = Memory(
        id=node_id,
        narrative=narrative,
        memory_type=MemoryType.ROOT,
        source="graph_integrator",
        metadata=metadata,
    )

    try:
        cortex.store(mem)
        logger.info(f"Created ROOT anchor node: {node_id}")
    except Exception as e:
        logger.error(f"Failed to create anchor node {node_id}: {e}")
        return ""

    return node_id


def integrate_graph(content_id: str) -> bool:
    """
    Main entry point: integrate FACT_CLOUD nodes into co-occurrence graph.

    Args:
        content_id: The content ID from blob_store

    Returns:
        True on success, False on failure
    """
    cortex = _get_cortex()
    if not cortex:
        logger.error("Failed to get cortex; cannot integrate graph")
        return False

    # Fetch blob metadata (title, author)
    from .blob_store import get_blob_metadata

    blob_meta = get_blob_metadata(content_id)
    if not blob_meta:
        logger.error(f"No blob metadata found for {content_id}")
        return False

    title = blob_meta.get("title", "Unknown")
    author = blob_meta.get("author", "")

    # Fetch all FACT_CLOUD nodes for this content
    fact_clouds = _fetch_fact_clouds(cortex, content_id)
    if not fact_clouds:
        logger.warning(f"No FACT_CLOUD nodes found for {content_id}")
        return False

    logger.info(f"Found {len(fact_clouds)} FACT_CLOUD nodes for {content_id}")

    # Create ROOT anchor node
    anchor_node_id = _create_book_anchor_node(cortex, content_id, title, author)
    if not anchor_node_id:
        logger.error("Failed to create anchor node")
        return False

    # Create edges from anchor to all FACT_CLOUD nodes
    try:
        for fc in fact_clouds:
            cortex.add_interpretive_edge(
                from_id=anchor_node_id,
                to_id=fc["id"],
                direction="activation",
                weight=fc["confidence"],  # weight from extraction confidence
                layer="book_fact",
            )
        logger.info(f"Created {len(fact_clouds)} edges from anchor to facts")
    except Exception as e:
        logger.error(f"Failed to create anchor edges: {e}")
        return False

    # Create co-occurrence edges between FACT_CLOUD nodes
    edge_count = 0
    try:
        for i, fc_a in enumerate(fact_clouds):
            for fc_b in fact_clouds[i + 1 :]:
                # Calculate weight based on chapter proximity
                weight = _calculate_edge_weight(
                    fc_a["chapter_idx"], fc_b["chapter_idx"]
                )

                # Create bidirectional edges
                cortex.add_interpretive_edge(
                    from_id=fc_a["id"],
                    to_id=fc_b["id"],
                    direction="co_deposited",
                    weight=weight,
                    meaning_payload="co-occurrence in same content",
                    layer="co_occurrence",
                )

                cortex.add_interpretive_edge(
                    from_id=fc_b["id"],
                    to_id=fc_a["id"],
                    direction="co_deposited",
                    weight=weight,
                    meaning_payload="co-occurrence in same content",
                    layer="co_occurrence",
                )

                edge_count += 2

        logger.info(f"Created {edge_count} co-occurrence edges")
    except Exception as e:
        logger.error(f"Failed to create co-occurrence edges: {e}")
        return False

    # Queue T-self-test with content_id
    try:
        _queue_self_test(content_id)
    except Exception as e:
        logger.error(f"Failed to queue T-self-test: {e}")
        # Don't fail the whole operation if queueing fails

    logger.info(
        f"Graph integration complete for {content_id}: {len(fact_clouds)} facts, {edge_count} co-occurrence edges"
    )
    return True


def _queue_self_test(content_id: str) -> None:
    """
    Signal that T-self-test should run with this content_id.

    T-self-test is already queued in the reading pipeline (D230/D231).
    The worker daemon will pick it up after this ticket completes.
    This is a logging checkpoint for diagnostic purposes.
    """
    logger.info(
        f"Graph integration complete: T-self-test ready with content_id={content_id}"
    )

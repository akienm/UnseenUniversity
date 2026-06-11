"""
reading_indexer.py — T-reading-indexer: chunk → G54 extract → FACT_CLOUD nodes (D230/D231)

Processes blob_store chapters into FACT_CLOUD graph nodes via G54 extraction.

Steps per chunk:
1. Read chunk from blob_store (chapter_idx, chunk_idx, text)
2. G54 extract: cloud LLM extracts factual nodes from chunk
3. Deposit each node as FACT_CLOUD_{hash} with metadata: {content_id, chapter, chunk_idx, source=reading}
4. On completion: queue T-graph-integrator with content_id

Key constraint: chunk size is chapter-aware (do not split across chapter boundaries).
Rate: configurable, default 30s between chunks.

Usage:
  from devices.igor.cognition.reading_indexer import index_content
  content_id = "<uuid>"
  index_content(content_id)  # → deposits FACT_CLOUD nodes + queues T-graph-integrator
"""

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..memory.cortex import Cortex
from ..memory.models import Memory, MemoryType
from .blob_store import get_blob_metadata, get_chunks
from ..igor_base import get_logger

logger = get_logger(__name__)

# ── Extraction prompt (similar to ebook_reader G54) ────────────────────────────

_INTERP_CANDIDATES = [
    ("CP1", "Epistemic honesty — say when uncertain"),
    ("CP2", "Failure is learning — FAIL = Further Advance In Learning"),
    ("CP3", "There's always a why — follow the causal chain"),
    ("CP4", "Make everything suck less for everybody — reduce friction"),
    ("CP5", "Respect the possibility of experience in all systems"),
    ("CP6", "Safety must be built and maintained — not default"),
]

_EXTRACTION_PROMPT = """\
You are extracting key ideas from a chunk of text from "{title}".

CHUNK (chapter {chapter_idx}):
{chunk_text}

Your task: identify whether this chunk contains fact(s) worth remembering as graph nodes.

Candidate interpretive nodes (choose the BEST match or "none"):
{candidates}

Respond with a JSON array of discovered facts (empty array if nothing to extract):
[
  {{
    "narrative": "1-2 sentences: a key idea or fact",
    "node_id": "best matching candidate or 'none'",
    "meaning_payload": "why this matters",
    "confidence": 0.0-1.0
  }},
  ...
]

Extract ONLY facts with confidence >= 0.6. Respond ONLY with the JSON array."""


# ── Extraction helpers ───────────────────────────────────────────────────────────


def _get_cortex() -> Optional[Cortex]:
    """Get cortex for current instance."""
    try:
        return Cortex(None)
    except Exception as e:
        logger.error(f"Failed to get cortex: {e}")
        return None


def _fact_cloud_id(
    content_id: str, chapter_idx: int, chunk_idx: int, fact_text: str
) -> str:
    """Generate stable ID for FACT_CLOUD node."""
    # Hash: content_id + chapter + chunk + first 50 chars of fact
    hashable = f"{content_id}|{chapter_idx}|{chunk_idx}|{fact_text[:50]}"
    h = hashlib.md5(hashable.encode()).hexdigest()[:8].upper()
    return f"FACT_CLOUD_{h}"


def _extract_facts_from_chunk(
    chunk_text: str,
    title: str,
    chapter_idx: int,
) -> list[dict]:
    """
    Call cloud LLM to extract facts from chunk.
    Returns list of {narrative, node_id, meaning_payload, confidence}.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set; skipping extraction")
        return []

    if len(chunk_text.strip()) < 20:
        logger.debug(f"Chunk too short ({len(chunk_text)} chars); skipping")
        return []

    try:
        import urllib.request as _urlreq

        from .inference_openrouter import OR_CHEAP_MODEL

        cheap_model = OR_CHEAP_MODEL
        candidates_str = "\n".join(
            f"  {nid}: {desc}" for nid, desc in _INTERP_CANDIDATES
        )
        prompt = _EXTRACTION_PROMPT.format(
            title=title[:50],
            chapter_idx=chapter_idx,
            chunk_text=chunk_text[:800],  # Limit to avoid token bloat
            candidates=candidates_str,
        )

        payload = json.dumps(
            {
                "model": cheap_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 500,
            }
        ).encode()

        req = _urlreq.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/akienm/TheIgors",
            },
            method="POST",
        )

        with _urlreq.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        result = data["choices"][0]["message"]["content"].strip()

        # Parse JSON array
        facts = json.loads(result)
        if not isinstance(facts, list):
            logger.warning(f"Expected JSON array, got: {type(facts)}")
            return []

        # Filter by confidence
        return [f for f in facts if f.get("confidence", 0) >= 0.6]

    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error in extraction: {e}")
        return []
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return []


def _deposit_fact(
    cortex: Cortex,
    content_id: str,
    chapter_idx: int,
    chunk_idx: int,
    fact: dict,
    title: str,
    author: str,
) -> str:
    """Deposit a single extracted fact as FACT_CLOUD node. Returns node ID."""
    narrative = fact.get("narrative", "").strip()
    node_id = fact.get("node_id", "none").strip()
    meaning_payload = fact.get("meaning_payload", "").strip()
    confidence = float(fact.get("confidence", 0.6))

    if not narrative or confidence < 0.6:
        return ""

    # Generate stable ID
    mem_id = _fact_cloud_id(content_id, chapter_idx, chunk_idx, narrative)

    # Build metadata with provenance
    metadata = {
        "content_id": content_id,
        "chapter_idx": chapter_idx,
        "chunk_idx": chunk_idx,
        "title": title[:100],
        "source_title": title[:100],
        "author": author[:50] if author else "",
        "source_author": author[:50] if author else "",
        "extraction_confidence": confidence,
        "matched_node": node_id if node_id != "none" else "",
        "model_used": os.getenv("OR_CHEAP_MODEL", "unknown"),
        "inference_tier": "cloud",
    }

    # Create memory
    mem = Memory(
        id=mem_id,
        narrative=narrative,
        memory_type=MemoryType.FACTUAL,
        source="reading_indexer",
        certainty=confidence,
        context_of_encoding=f"blob_extract|{title[:40]}|ch{chapter_idx}|chunk{chunk_idx}",
        metadata=metadata,
    )

    # Store it
    try:
        cortex.store(mem)
        logger.info(f"Deposited {mem_id}: {narrative[:60]}")

        # Link to matched interpretive node if present
        if node_id and node_id != "none":
            try:
                parent = cortex.get(node_id)
                if parent:
                    cortex.add_child(node_id, mem_id)
                    if meaning_payload:
                        cortex.add_interpretive_edge(
                            from_id=node_id,
                            to_id=mem_id,
                            direction="activation",
                            condition_csb=f"context:reading|blob:{content_id[:8]}",
                            meaning_payload=meaning_payload,
                            action_pointer="",
                            weight=confidence,
                        )
            except Exception as e:
                logger.warning(f"Failed to link to {node_id}: {e}")

        return mem_id
    except Exception as e:
        logger.error(f"Failed to deposit {mem_id}: {e}")
        return ""


def index_content(
    content_id: str,
    rate_limit_secs: float = 30.0,
    dry_run: bool = False,
) -> dict:
    """
    Process blob content: extract facts from all chunks, deposit FACT_CLOUD nodes.

    Args:
        content_id: UUID of blob in blob_store
        rate_limit_secs: seconds between LLM calls (default 30)
        dry_run: if True, plan only, don't write

    Returns:
        {
            "content_id": content_id,
            "status": "success" | "error",
            "chunks_processed": int,
            "facts_extracted": int,
            "nodes_deposited": int,
            "error": str or None,
        }
    """
    cortex = _get_cortex()
    if cortex is None:
        return {
            "content_id": content_id,
            "status": "error",
            "error": "Could not initialize cortex",
            "chunks_processed": 0,
            "facts_extracted": 0,
            "nodes_deposited": 0,
        }

    # Get blob metadata
    metadata = get_blob_metadata(content_id)
    if not metadata:
        return {
            "content_id": content_id,
            "status": "error",
            "error": f"Content {content_id} not found in blob_store",
            "chunks_processed": 0,
            "facts_extracted": 0,
            "nodes_deposited": 0,
        }

    title = metadata.get("title", "Unknown")
    author = metadata.get("author", "")

    logger.info(f"Indexing {content_id}: {title}")

    # Get all chunks
    chunks = get_chunks(content_id)
    if not chunks:
        logger.warning(f"No chunks found for {content_id}")
        return {
            "content_id": content_id,
            "status": "success",
            "chunks_processed": 0,
            "facts_extracted": 0,
            "nodes_deposited": 0,
        }

    chunks_processed = 0
    facts_extracted = 0
    nodes_deposited = 0

    # Process each chunk
    for chunk in chunks:
        chapter_idx = chunk.get("chapter_idx")
        chunk_idx = chunk.get("chunk_idx")
        text = chunk.get("text", "")

        if not text:
            continue

        if dry_run:
            logger.info(f"[DRY] Would process ch{chapter_idx} chunk{chunk_idx}")
            chunks_processed += 1
            continue

        logger.info(f"Extracting ch{chapter_idx} chunk{chunk_idx} ({len(text)} chars)")

        # Extract facts
        facts = _extract_facts_from_chunk(text, title, chapter_idx)
        facts_extracted += len(facts)

        # Deposit each fact
        for fact in facts:
            node_id = _deposit_fact(
                cortex, content_id, chapter_idx, chunk_idx, fact, title, author
            )
            if node_id:
                nodes_deposited += 1

        chunks_processed += 1

        # Rate limit between chunks
        if chunks_processed < len(chunks):
            logger.debug(f"Waiting {rate_limit_secs}s before next chunk")
            time.sleep(rate_limit_secs)

    # Update blob_index status
    try:
        from .blob_store import _update_blob_index

        _update_blob_index(content_id, "indexed")
    except Exception as e:
        logger.warning(f"Failed to update blob_index: {e}")

    logger.info(
        f"Indexing complete: {chunks_processed} chunks, "
        f"{facts_extracted} facts, {nodes_deposited} nodes deposited"
    )

    return {
        "content_id": content_id,
        "status": "success",
        "chunks_processed": chunks_processed,
        "facts_extracted": facts_extracted,
        "nodes_deposited": nodes_deposited,
    }


def queue_graph_integrator(content_id: str) -> bool:
    """
    Queue T-graph-integrator for post-processing.
    Called after index_content() completes successfully.
    Returns True if queued successfully.
    """
    try:
        # Import queue module
        sys.path.insert(
            0, str(Path(__file__).parent.parent.parent.parent / "lab" / "claudecode")
        )
        from cc_queue import mark_pending

        # Add T-graph-integrator task
        ticket_id = f"T-graph-integrator-{content_id[:8]}"
        mark_pending(
            ticket_id,
            title=f"Graph integrator for {content_id[:8]}",
            description=f"Post-processing for indexed blob {content_id}",
            priority=6,
        )
        logger.info(f"Queued {ticket_id}")
        return True
    except Exception as e:
        logger.warning(f"Failed to queue graph integrator: {e}")
        return False

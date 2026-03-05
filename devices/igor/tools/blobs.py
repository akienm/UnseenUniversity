"""
Tagged blob storage tools — #65.

Igor can store full-content reference documents (research notes, code,
transcripts) with tags. Normal memory search discovers them via their
brief narrative; tag search retrieves the full content on demand.
"""

import json
import os
from pathlib import Path

from .registry import Tool, registry


def _get_cortex():
    """Get the cortex singleton from the running Igor instance."""
    db_path = os.getenv("IGOR_DB_PATH", "memory/igor.db")
    from ..memory.cortex import Cortex
    return Cortex(Path(db_path))


def store_reference(
    narrative: str,
    content: str,
    tags: str,
    **_,
) -> str:
    """
    Store a reference document with full content and tags.

    narrative: brief description (what this is, max 1-2 sentences)
    content: full text to preserve verbatim
    tags: comma-separated list of tags (e.g. "neuroscience,baddeley,working-memory")
    """
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        cortex = _get_cortex()
        mem = cortex.store_blob(
            narrative=narrative,
            content=content,
            tags=tag_list,
        )
        return (
            f"Stored reference blob '{mem.id}'.\n"
            f"  Narrative: {narrative[:80]}\n"
            f"  Tags: {', '.join(tag_list)}\n"
            f"  Content length: {len(content)} chars\n"
            f"Retrieve with: get_reference(memory_id='{mem.id}') "
            f"or search_references(tags='{tags}')"
        )
    except Exception as e:
        return f"[ERROR storing blob] {e}"


def get_reference(memory_id: str, **_) -> str:
    """Retrieve full content of a reference blob by memory ID."""
    try:
        cortex = _get_cortex()
        content = cortex.get_blob(memory_id)
        if content is None:
            return f"No blob found for memory_id='{memory_id}'"
        mem = cortex.get(memory_id)
        header = f"[{memory_id}] {mem.narrative if mem else '(narrative not found)'}\n\n"
        return header + content
    except Exception as e:
        return f"[ERROR retrieving blob] {e}"


def search_references(tags: str, match_all: bool = False, **_) -> str:
    """
    Search reference blobs by tag.

    tags: comma-separated tags to search for
    match_all: if true, only return blobs matching ALL tags (default: any)
    """
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        cortex = _get_cortex()
        results = cortex.search_by_tags(tag_list, match_all=bool(match_all))
        if not results:
            return f"No reference blobs found matching tags: {', '.join(tag_list)}"
        lines = [f"Found {len(results)} reference blob(s) matching [{', '.join(tag_list)}]:"]
        for r in results:
            lines.append(
                f"\n  [{r['memory_id']}] {r['narrative'][:80]}\n"
                f"    Tags: {', '.join(r['tags'])}\n"
                f"    Preview: {r['content_preview'][:120]}...\n"
                f"    Use get_reference(memory_id='{r['memory_id']}') for full content"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR searching blobs] {e}"


def list_reference_tags(**_) -> str:
    """List all tags used across reference blobs, with counts."""
    try:
        cortex = _get_cortex()
        counts = cortex.list_blob_tags()
        if not counts:
            return "No reference blobs stored yet."
        lines = ["Reference blob tags (most used first):"]
        for tag, count in counts.items():
            lines.append(f"  {tag}: {count}")
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR listing tags] {e}"


# ── Register tools ─────────────────────────────────────────────────────────────

registry.register(Tool(
    name="store_reference",
    description=(
        "Store a reference document (research notes, code, transcripts, full articles) "
        "with a brief narrative description and comma-separated tags. "
        "The narrative participates in normal memory search; "
        "the full content is retrievable by tag or memory ID. "
        "Use this when you want to preserve something verbatim for later retrieval."
    ),
    parameters={
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": "Brief description: what this is and why it matters (1-2 sentences)",
            },
            "content": {
                "type": "string",
                "description": "Full verbatim content to preserve",
            },
            "tags": {
                "type": "string",
                "description": "Comma-separated tags, e.g. 'neuroscience,baddeley,working-memory'",
            },
        },
        "required": ["narrative", "content", "tags"],
    },
    fn=store_reference,
))

registry.register(Tool(
    name="get_reference",
    description="Retrieve full content of a stored reference blob by its memory ID.",
    parameters={
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "The 8-character memory ID returned when the blob was stored",
            },
        },
        "required": ["memory_id"],
    },
    fn=get_reference,
))

registry.register(Tool(
    name="search_references",
    description=(
        "Search stored reference blobs by tag. "
        "Returns matching blobs with previews and memory IDs. "
        "Use get_reference() to fetch full content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tags": {
                "type": "string",
                "description": "Comma-separated tags to search for",
            },
            "match_all": {
                "type": "boolean",
                "description": "If true, only return blobs matching ALL tags (default: any tag matches)",
            },
        },
        "required": ["tags"],
    },
    fn=search_references,
))

registry.register(Tool(
    name="list_reference_tags",
    description="List all tags used across stored reference blobs, with usage counts.",
    parameters={"type": "object", "properties": {}, "required": []},
    fn=list_reference_tags,
))

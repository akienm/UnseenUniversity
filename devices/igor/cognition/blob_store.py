"""
Blob store — persistent storage for raw acquired content (D230).

Schema:
  blobs table: content_id (uuid), title, author, source_channel, acquired_at, format
  blob_chunks table: content_id, chapter_title, chapter_idx, chunk_idx, text, char_start, char_end

Chapter boundaries are preserved to enable proximity-weighted co-occurrence edges
in the graph integrator (edges within chapter > edges across chapters).

blob_index.json — lightweight manifest for tracking processing status.
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from wild_igor.igor.memory.db_proxy import make_home_proxy

logger = logging.getLogger(__name__)


def _get_instance_dir() -> Path:
    """Get the instance directory."""
    from wild_igor.igor.paths import paths as _paths

    return _paths().instance


def _get_blob_index_path() -> Path:
    """Get the path to blob_index.json."""
    instance_dir = _get_instance_dir()
    return instance_dir / "blob_index.json"


def _init_blob_tables() -> None:
    """Initialize blob storage tables if they don't exist."""
    proxy = make_home_proxy()

    # Create blobs table
    proxy.execute("""
        CREATE TABLE IF NOT EXISTS blobs (
            content_id  TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            author      TEXT,
            source_channel TEXT,
            acquired_at TEXT,
            format      TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

    # Create blob_chunks table
    proxy.execute("""
        CREATE TABLE IF NOT EXISTS blob_chunks (
            id              SERIAL PRIMARY KEY,
            content_id      TEXT NOT NULL,
            chapter_title   TEXT,
            chapter_idx     INTEGER,
            chunk_idx       INTEGER,
            text            TEXT NOT NULL,
            char_start      INTEGER,
            char_end        INTEGER,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (content_id) REFERENCES blobs(content_id) ON DELETE CASCADE
        )
        """)

    # Create indexes
    proxy.execute(
        "CREATE INDEX IF NOT EXISTS idx_blob_chunks_content_id ON blob_chunks(content_id)"
    )
    proxy.execute(
        "CREATE INDEX IF NOT EXISTS idx_blob_chunks_chapter ON blob_chunks(content_id, chapter_idx)"
    )


def _chunk_by_chapters(content: str) -> list[dict]:
    """
    Chunk content by chapter boundaries.

    Returns list of dicts: {"title": str, "idx": int, "chunks": [{"text": str, "start": int, "end": int}]}
    """
    # Heuristic: look for chapter markers (Chapter N, # Chapter, etc.)
    chapter_pattern = r"^(?:Chapter\s+\d+|#+\s+.*?)$"
    lines = content.split("\n")

    chapters = []
    current_chapter_title = "(intro)"
    current_chapter_idx = 0
    current_chapter_start = 0
    current_chapter_text = []

    for i, line in enumerate(lines):
        char_pos = sum(len(l) + 1 for l in lines[:i])  # +1 for newline

        if re.match(chapter_pattern, line.strip()):
            # Found a chapter marker — save previous chapter if it has content
            if current_chapter_text:
                chapter_text = "\n".join(current_chapter_text)
                chapters.append(
                    {
                        "title": current_chapter_title,
                        "idx": current_chapter_idx,
                        "text": chapter_text,
                        "char_start": current_chapter_start,
                        "char_end": char_pos,
                    }
                )
            # Start new chapter
            current_chapter_title = line.strip()
            current_chapter_idx += 1
            current_chapter_start = char_pos
            current_chapter_text = [line]
        else:
            current_chapter_text.append(line)

    # Save final chapter
    if current_chapter_text:
        chapter_text = "\n".join(current_chapter_text)
        chapters.append(
            {
                "title": current_chapter_title,
                "idx": current_chapter_idx,
                "text": chapter_text,
                "char_start": current_chapter_start,
                "char_end": len(content),
            }
        )

    return chapters


def store_blob_chapters(
    content: str,
    title: str,
    author: Optional[str] = None,
    source_channel: str = "unknown",
    format: str = "text",
) -> str:
    """
    Store content with chapter-aware chunking.

    Args:
        content: Full text content
        title: Document title
        author: Author name (optional)
        source_channel: Channel name (e.g., "DirectURLChannel")
        format: Content format (e.g., "text", "epub", "pdf")

    Returns:
        content_id (UUID string)
    """
    _init_blob_tables()

    content_id = str(uuid.uuid4())
    acquired_at = datetime.now(timezone.utc).isoformat()

    proxy = make_home_proxy()

    try:
        # Insert blob metadata
        proxy.execute(
            """
            INSERT INTO blobs (content_id, title, author, source_channel, acquired_at, format)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (content_id, title, author, source_channel, acquired_at, format),
        )

        # Chunk by chapters and store chunks
        chapters = _chunk_by_chapters(content)
        for chapter in chapters:
            # Split chapter into smaller chunks (e.g., every 10KB)
            chapter_text = chapter["text"]
            chunk_size = 10000  # Characters per chunk
            for chunk_idx, i in enumerate(range(0, len(chapter_text), chunk_size)):
                chunk_text = chapter_text[i : i + chunk_size]
                char_start = chapter["char_start"] + i
                char_end = min(char_start + len(chunk_text), chapter["char_end"])

                proxy.execute(
                    """
                    INSERT INTO blob_chunks
                    (content_id, chapter_title, chapter_idx, chunk_idx, text, char_start, char_end)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        content_id,
                        chapter["title"],
                        chapter["idx"],
                        chunk_idx,
                        chunk_text,
                        char_start,
                        char_end,
                    ),
                )

        logger.info(
            f"store_blob_chapters: stored '{title}' ({len(content)} chars) "
            f"in {len(chapters)} chapters → content_id={content_id}"
        )

        # Update blob_index.json
        _update_blob_index(content_id, "acquired")

        return content_id

    except Exception as e:
        logger.error(f"store_blob_chapters: error storing blob: {e}")
        raise


def get_blob_metadata(content_id: str) -> Optional[dict]:
    """Get metadata about a stored blob."""
    proxy = make_home_proxy()
    result = proxy.query_one(
        "SELECT title, author, source_channel, acquired_at, format FROM blobs WHERE content_id = %s",
        (content_id,),
    )
    if result:
        return {
            "content_id": content_id,
            "title": result[0],
            "author": result[1],
            "source_channel": result[2],
            "acquired_at": result[3],
            "format": result[4],
        }
    return None


def get_chunks(content_id: str, chapter_idx: Optional[int] = None) -> list[dict]:
    """
    Get chunks for a blob, optionally filtered by chapter.

    Returns list of dicts: {"chapter_title": str, "chapter_idx": int, "chunk_idx": int, "text": str}
    """
    proxy = make_home_proxy()

    if chapter_idx is not None:
        results = proxy.query(
            """
            SELECT chapter_title, chapter_idx, chunk_idx, text
            FROM blob_chunks
            WHERE content_id = %s AND chapter_idx = %s
            ORDER BY chapter_idx, chunk_idx
            """,
            (content_id, chapter_idx),
        )
    else:
        results = proxy.query(
            """
            SELECT chapter_title, chapter_idx, chunk_idx, text
            FROM blob_chunks
            WHERE content_id = %s
            ORDER BY chapter_idx, chunk_idx
            """,
            (content_id,),
        )

    return [
        {
            "chapter_title": r[0],
            "chapter_idx": r[1],
            "chunk_idx": r[2],
            "text": r[3],
        }
        for r in results
    ]


def _update_blob_index(content_id: str, status: str) -> None:
    """
    Update blob_index.json with content_id and status.

    status: acquired | indexed | graphed | tested
    """
    index_path = _get_blob_index_path()

    # Load existing index
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text())
        except json.JSONDecodeError:
            index = {}
    else:
        index = {}

    # Ensure the blob is in the index
    if content_id not in index:
        metadata = get_blob_metadata(content_id)
        if metadata:
            index[content_id] = {
                "title": metadata["title"],
                "author": metadata.get("author"),
                "status": status,
                "created_at": metadata["acquired_at"],
            }
        else:
            index[content_id] = {
                "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
    else:
        # Update status
        index[content_id]["status"] = status

    # Write back
    index_path.write_text(json.dumps(index, indent=2))
    logger.info(f"blob_index updated: {content_id} → {status}")

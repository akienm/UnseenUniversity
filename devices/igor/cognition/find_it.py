"""
Find-it — content router (D230, D231).

Routes acquisition requests to the appropriate channel based on provided parameters.
Returns a content_id (memory blob ID) on success, or failure message on all-channels-fail.

Priority order:
1. FileInboxChannel (if file_path provided, short-circuits)
2. DirectURLChannel (if url provided, short-circuits)
3. CalibreChannel (local EPUB search)
4. GeminiSearchChannel (free web search, if research_mode=True)
5. BrowserUseChannel (last resort with constraints)
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from wild_igor.igor.network.channels import (
    AcquireRequest,
    AcquireResult,
    ChannelFailure,
    get_registry,
)

logger = logging.getLogger(__name__)


def _get_cortex():
    """Get the cortex singleton from the running Igor instance."""
    db_path = os.getenv("IGOR_DB_PATH", "memory/igor.db")
    from wild_igor.igor.memory.cortex import Cortex

    return Cortex(Path(db_path))


def find_content(
    title: Optional[str] = None,
    author: Optional[str] = None,
    url: Optional[str] = None,
    file_path: Optional[str] = None,
    research_mode: bool = False,
) -> str:
    """
    Router for acquisition requests — tries channels in priority order.

    Args:
        title: Book/document title (search term)
        author: Author name (search modifier)
        url: Direct URL to fetch
        file_path: Path to local file to read
        research_mode: If True, allow GeminiSearch; if False, skip it

    Returns:
        content_id (8-char memory ID) on success.
        Error message (starting with "[ERROR") on failure.
    """
    # Build query from provided parameters
    query_parts = []
    if title:
        query_parts.append(title)
    if author:
        query_parts.append(f"by {author}")
    if url:
        query_parts.append(f"url: {url}")
    if file_path:
        query_parts.append(f"file: {file_path}")

    query = " ".join(query_parts) if query_parts else "(no query)"

    # Build AcquireRequest
    request = AcquireRequest(
        query=query,
        context={
            "title": title,
            "author": author,
            "url": url,
            "file_path": file_path,
            "research_mode": research_mode,
        },
    )

    # Build skip_channels set based on priority rules
    skip_channels = set()

    # Short-circuit logic: if explicit file_path, only use FileInboxChannel
    if file_path and not url:
        # Skip everything except FileInboxChannel
        skip_channels = {
            "DirectURLChannel",
            "CalibreChannel",
            "GeminiSearchChannel",
            "BrowserUseChannel",
        }

    # Short-circuit logic: if explicit url, only use DirectURLChannel
    elif url and not file_path:
        # Skip everything except DirectURLChannel
        skip_channels = {
            "FileInboxChannel",
            "CalibreChannel",
            "GeminiSearchChannel",
            "BrowserUseChannel",
        }

    # Research mode: skip GeminiSearchChannel if research_mode=False
    else:
        if not research_mode:
            skip_channels.add("GeminiSearchChannel")

    # Try to acquire content
    try:
        registry = get_registry()
        result, channel_used = registry.acquire(request, skip_channels=skip_channels)

        if isinstance(result, AcquireResult):
            # Success — store blob and return content_id
            cortex = _get_cortex()
            title_str = title or query
            memory = cortex.store_blob(
                narrative=f"Acquired content: {title_str[:80]} (via {channel_used})",
                content=result.blob.decode("utf-8", errors="replace"),
                tags=["acquisition", channel_used.lower(), "reading_pipeline"],
            )

            logger.info(
                f"find_content: acquired '{title_str[:40]}' via {channel_used} "
                f"({result.cost_usd:.4f} USD) → content_id={memory.id}"
            )

            return memory.id

        else:
            # Failure — all channels exhausted
            error_msg = f"[ERROR find_content] All channels failed: {result.reason}"
            logger.warning(error_msg)

            # Log to watchlist (T-watchlist-notifications will pick this up)
            try:
                cortex = _get_cortex()
                cortex.store_blob(
                    narrative=f"Acquisition failure: {title or query}",
                    content=f"Failed to acquire: {result.reason}\nQuery: {query}\nTime: {datetime.now(timezone.utc).isoformat()}",
                    tags=["acquisition_failure", "watchlist", "needs_review"],
                )
            except Exception as e:
                logger.warning(f"Could not log failure to watchlist: {e}")

            return error_msg

    except Exception as e:
        error_msg = (
            f"[ERROR find_content] Unexpected error: {type(e).__name__}: {str(e)[:200]}"
        )
        logger.error(error_msg)
        return error_msg

"""
FileInboxChannel — read files from ~/.TheIgors/Igor-wild-0001/inbox/.

Short-circuits further search (explicit file drop = user's direct request).
Free, high reliability.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import (
    Channel,
    ChannelReliability,
    AcquireRequest,
    AcquireResult,
    ChannelFailure,
    BlobMeta,
)
from ...paths import paths


class FileInboxChannel(Channel):
    """
    Read dropped files from inbox directory.

    Query is expected to be a filename (exact or substring match).
    Matches are tried in filesystem order.
    """

    def __init__(self):
        super().__init__(
            name="FileInboxChannel",
            constraints=[],
            cost_per_call_usd=0.0,
            reliability=ChannelReliability.HIGH,
            one_way=False,
            short_circuits=True,  # Explicit file = user's direct request
        )

    def acquire(self, request: AcquireRequest) -> AcquireResult | ChannelFailure:
        """
        Search inbox for a file and return its content.

        The query is a filename or filename substring.
        """
        try:
            inbox = paths().inbox
            inbox.mkdir(parents=True, exist_ok=True)

            query = request.query.strip().lower()
            if not query:
                return ChannelFailure(
                    channel_name=self.name,
                    reason="Empty filename query",
                    cost_usd=0.0,
                )

            # Find matching files
            matches = [
                f for f in inbox.iterdir() if f.is_file() and query in f.name.lower()
            ]
            if not matches:
                return ChannelFailure(
                    channel_name=self.name,
                    reason=f"No files in inbox matching '{query}'",
                    cost_usd=0.0,
                )

            # Use first match
            file_path = matches[0]

            # Read file
            try:
                blob = file_path.read_bytes()
            except Exception as e:
                return ChannelFailure(
                    channel_name=self.name,
                    reason=f"Could not read {file_path.name}: {str(e)[:100]}",
                    cost_usd=0.0,
                )

            # Detect format from extension
            suffix = file_path.suffix.lower()
            format_map = {
                ".epub": "epub",
                ".pdf": "pdf",
                ".mobi": "mobi",
                ".azw": "azw",
                ".azw3": "azw3",
                ".html": "html",
                ".txt": "text",
                ".md": "markdown",
            }
            fmt = format_map.get(suffix, "unknown")

            meta = BlobMeta(
                title=file_path.stem,
                source=self.name,
                file_path=str(file_path),
                format=fmt,
                size_bytes=len(blob),
                retrieved_at=datetime.utcnow().isoformat() + "Z",
            )

            return AcquireResult(
                blob=blob,
                meta=meta,
                cost_usd=0.0,
            )

        except Exception as e:
            return ChannelFailure(
                channel_name=self.name,
                reason=f"Error: {type(e).__name__}: {str(e)[:200]}",
                cost_usd=0.0,
            )

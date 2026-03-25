"""
CalibreChannel — local EPUB library search via ebook_reader.

Uses Igor's ebook_reader tool to search and read from Calibre library.
Free, high reliability, one_way (exhausts local results, doesn't search web).
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


class CalibreChannel(Channel):
    """
    Search local Calibre library for ebooks.

    Uses ebook_reader.find_book() to search by title/author/ASIN.
    On match, opens the book and reads the first chapter as a sample.
    """

    def __init__(self):
        super().__init__(
            name="CalibreChannel",
            constraints=[],
            cost_per_call_usd=0.0,
            reliability=ChannelReliability.HIGH,
            one_way=True,  # Local search exhausts available results
            short_circuits=False,
        )

    def acquire(self, request: AcquireRequest) -> AcquireResult | ChannelFailure:
        """
        Search Calibre for a book and return the first chapter as bytes.

        The query is expected to be a book title or author name.
        """
        try:
            from ...tools.ebook_reader import find_book, open_book, read_chunk

            query = request.query.strip()
            if not query:
                return ChannelFailure(
                    channel_name=self.name,
                    reason="Empty query",
                    cost_usd=0.0,
                )

            # Search Calibre
            books = find_book(query)
            if not books:
                return ChannelFailure(
                    channel_name=self.name,
                    reason=f"No books found for '{query}' in Calibre",
                    cost_usd=0.0,
                )

            # Open first match
            book_meta = books[0]
            handle = open_book(book_meta.title, book_meta.author)

            # Read first chunk (sample)
            sentences = read_chunk(handle, n=10)  # First 10 sentences
            if not sentences:
                return ChannelFailure(
                    channel_name=self.name,
                    reason=f"Could not read content from '{book_meta.title}'",
                    cost_usd=0.0,
                )

            content = "\n".join(sentences)
            blob = content.encode("utf-8")

            meta = BlobMeta(
                title=book_meta.title,
                source=self.name,
                file_path=str(book_meta.path),
                format=book_meta.fmt,
                size_bytes=len(blob),
                retrieved_at=datetime.utcnow().isoformat() + "Z",
            )

            return AcquireResult(
                blob=blob,
                meta=meta,
                cost_usd=0.0,
            )

        except ImportError:
            return ChannelFailure(
                channel_name=self.name,
                reason="ebook_reader tools not available",
                cost_usd=0.0,
            )
        except Exception as e:
            return ChannelFailure(
                channel_name=self.name,
                reason=f"Error: {type(e).__name__}: {str(e)[:200]}",
                cost_usd=0.0,
            )

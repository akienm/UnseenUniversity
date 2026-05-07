"""
DirectURLChannel — fetch explicit URLs or file paths directly.

Query is expected to be a URL (http/https) or file path (/path/to/file).
Short-circuits further search (explicit URL/path = user's direct request).
Free for local files, variable cost for web (uses NetworkProxy).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from ..igor_base import IgorBase
from . import (
    Channel,
    ChannelReliability,
    AcquireRequest,
    AcquireResult,
    ChannelFailure,
    BlobMeta,
)


class DirectURLChannel(Channel, IgorBase):
    """
    Fetch content from explicit URL or file path.

    Query can be:
      - http://example.com/file.pdf
      - https://example.com/page
      - /path/to/local/file
      - ~/path/to/file
    """

    def __init__(self):
        super().__init__(
            name="DirectURLChannel",
            constraints=[],
            cost_per_call_usd=0.0,
            reliability=ChannelReliability.HIGH,
            one_way=False,
            short_circuits=True,  # Explicit URL/path = user's direct request
        )

    def acquire(self, request: AcquireRequest) -> AcquireResult | ChannelFailure:
        """
        Fetch content from the given URL or file path.
        """
        try:
            query = request.query.strip()
            if not query:
                return ChannelFailure(
                    channel_name=self.name,
                    reason="Empty URL/path query",
                    cost_usd=0.0,
                )

            # Try as URL first
            if query.startswith(("http://", "https://")):
                return self._fetch_url(query)

            # Try as file path
            if query.startswith(("/", "~", ".")):
                return self._fetch_file(query)

            # Ambiguous — try file first, then URL
            result = self._fetch_file(query)
            if isinstance(result, AcquireResult):
                return result

            # File failed, try URL (don't add scheme — let it fail naturally)
            return ChannelFailure(
                channel_name=self.name,
                reason=f"'{query}' is neither an accessible file nor a valid URL",
                cost_usd=0.0,
            )

        except Exception as e:
            return ChannelFailure(
                channel_name=self.name,
                reason=f"Error: {type(e).__name__}: {str(e)[:200]}",
                cost_usd=0.0,
            )

    def _fetch_file(self, path_str: str) -> AcquireResult | ChannelFailure:
        """Fetch content from a local file."""
        try:
            path = Path(path_str).expanduser().resolve()

            if not path.exists():
                return ChannelFailure(
                    channel_name=self.name,
                    reason=f"File not found: {path}",
                    cost_usd=0.0,
                )

            if not path.is_file():
                return ChannelFailure(
                    channel_name=self.name,
                    reason=f"Not a file: {path}",
                    cost_usd=0.0,
                )

            blob = path.read_bytes()

            # Detect format from extension
            suffix = path.suffix.lower()
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
                title=path.stem,
                source=self.name,
                file_path=str(path),
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
                reason=f"Error reading file: {str(e)[:200]}",
                cost_usd=0.0,
            )

    def _fetch_url(self, url: str) -> AcquireResult | ChannelFailure:
        """Fetch content from a URL."""
        try:
            from ..tools.system_proxy import system_proxy

            response = system_proxy.network.get(url, timeout=30)
            if response is None:
                return ChannelFailure(
                    channel_name=self.name,
                    reason=f"Failed to fetch {url} (no response)",
                    cost_usd=0.0,
                )

            blob = response

            # Extract title from URL
            parsed = urlparse(url)
            title = Path(parsed.path).stem or parsed.netloc or "content"

            # Guess format from URL extension or content-type
            suffix = Path(parsed.path).suffix.lower()
            format_map = {
                ".epub": "epub",
                ".pdf": "pdf",
                ".html": "html",
                ".txt": "text",
            }
            fmt = format_map.get(suffix, "unknown")

            meta = BlobMeta(
                title=title,
                source=self.name,
                url=url,
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
                reason=f"Error fetching URL: {str(e)[:200]}",
                cost_usd=0.0,
            )

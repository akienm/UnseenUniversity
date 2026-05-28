"""
ReaderDevice — unified URI fetch + blob cache + output modes.

T-reader-uri-resolver: fetch/cache foundation (fetch_uri, BlobCache).
T-reader-summary-mode: ReaderDevice(BaseDevice) + format=summary output.
T-reader-node-mode: format=nodes output (not yet implemented).
"""

from .blob_cache import BlobCache, BlobEntry
from .chunker import chunk_text
from .device import ReaderDevice
from .uri import FetchResult, fetch_uri

__all__ = [
    "fetch_uri",
    "FetchResult",
    "BlobCache",
    "BlobEntry",
    "chunk_text",
    "ReaderDevice",
]

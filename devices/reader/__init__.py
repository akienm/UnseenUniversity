"""
ReaderDevice — unified URI fetch + blob cache + output modes.

T-reader-uri-resolver ships this package with the fetch/cache foundation.
T-reader-summary-mode adds ReaderDevice(BaseDevice) + format=summary output.
T-reader-node-mode adds format=nodes output.
"""

from .blob_cache import BlobCache, BlobEntry
from .uri import FetchResult, fetch_uri

__all__ = ["fetch_uri", "FetchResult", "BlobCache", "BlobEntry"]

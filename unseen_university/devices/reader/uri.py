"""
uri.py — URI scheme handlers + fetch pipeline for ReaderDevice.

Supported schemes:
  https://      — urllib fetch, HTML stripped, 4000-char text limit
  http://       — same as https
  file:///path  — direct filesystem read
  calibre://ID  — Calibre library lookup via metadata.db → file bytes
  blob://sha256:<hex>  — direct cache hit, skip fetch

All handlers return raw bytes + content_type. fetch_uri() caches via
BlobCache and returns a FetchResult with content (text) and provenance.

comms:// deferred to v2 — URI resolver pattern accommodates it cleanly.
"""

from __future__ import annotations

import html.parser
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .blob_cache import BlobCache

from .blob_cache import BlobCache as _BlobCache

log = logging.getLogger(__name__)

_FETCH_TIMEOUT_SEC = int(os.environ.get("READER_FETCH_TIMEOUT", "15"))
_CONTENT_MAX_CHARS = int(os.environ.get("READER_CONTENT_MAX_CHARS", "4000"))

# Default Calibre library location — override via CALIBRE_LIBRARY_PATH
_DEFAULT_CALIBRE_PATH = Path(
    os.environ.get(
        "CALIBRE_LIBRARY_PATH",
        Path("/media/akien/onedrive/AkiensMedia/Ebooks"),
    )
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── HTML stripping ─────────────────────────────────────────────────────────────


class _HTMLTextExtractor(html.parser.HTMLParser):
    _SKIP_TAGS = frozenset({"script", "style", "head", "nav", "footer", "aside"})

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data.strip())

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(raw: bytes, max_chars: int = _CONTENT_MAX_CHARS) -> str:
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = raw.decode("latin-1", errors="replace")
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(text)
        result = extractor.get_text()
    except Exception:
        result = text
    return result[:max_chars]


def _text_from_raw(raw: bytes, content_type: str) -> str:
    """Extract text string from raw bytes based on content_type."""
    ct = content_type.split(";")[0].strip().lower()
    if "html" in ct:
        return _strip_html(raw)
    if ct.startswith("text/"):
        try:
            return raw.decode("utf-8", errors="replace")[:_CONTENT_MAX_CHARS]
        except Exception:
            return raw.decode("latin-1", errors="replace")[:_CONTENT_MAX_CHARS]
    # Binary formats (epub, pdf, mobi): text extraction handled by output modes
    return ""


# ── FetchResult ────────────────────────────────────────────────────────────────


@dataclass
class FetchResult:
    """Output of fetch_uri — content + provenance for any URI scheme."""

    uri: str
    content: str  # text representation (empty for binary like epub/pdf)
    sha256: str  # hex digest of raw bytes
    content_type: str
    size_bytes: int
    from_cache: bool
    blob_path: Path | None  # path to .blob.bin (None if not cached)
    fetched_at: str  # ISO timestamp


# ── Scheme handlers ────────────────────────────────────────────────────────────


def _fetch_http(uri: str) -> tuple[bytes, str]:
    """https:// or http:// → (raw_bytes, content_type)."""
    req = urllib.request.Request(
        uri, headers={"User-Agent": "UnseenUniversity-ReaderDevice/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
            ct = resp.headers.get("Content-Type", "text/html").split(";")[0].strip()
            return resp.read(), ct
    except urllib.error.HTTPError as e:
        raise OSError(f"HTTP {e.code} fetching {uri}") from e
    except urllib.error.URLError as e:
        raise OSError(f"URL error fetching {uri}: {e.reason}") from e


def _fetch_file(uri: str) -> tuple[bytes, str]:
    """file:///path → (raw_bytes, content_type)."""
    parsed = urllib.parse.urlparse(uri)
    path = Path(urllib.request.url2pathname(parsed.path))
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    raw = path.read_bytes()
    ext = path.suffix.lower()
    ct_map = {
        ".html": "text/html",
        ".htm": "text/html",
        ".txt": "text/plain",
        ".pdf": "application/pdf",
        ".epub": "application/epub+zip",
        ".mobi": "application/x-mobipocket-ebook",
    }
    return raw, ct_map.get(ext, "application/octet-stream")


def _fetch_calibre(uri: str, library_path: Path | None = None) -> tuple[bytes, str]:
    """calibre://ID → (raw_bytes, content_type) via metadata.db."""
    import sqlite3

    lib = library_path or _DEFAULT_CALIBRE_PATH
    db_path = lib / "metadata.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Calibre library not found: {db_path}")

    # calibre://12345 — book ID is netloc or path
    parsed = urllib.parse.urlparse(uri)
    raw_id = parsed.netloc or parsed.path.lstrip("/")
    try:
        book_id = int(raw_id)
    except ValueError:
        raise ValueError(f"calibre:// ID must be an integer, got: {raw_id!r}")

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT path FROM books WHERE id = ?", (book_id,)).fetchone()

    if not row:
        raise KeyError(f"Book {book_id} not found in Calibre library at {lib}")

    book_dir = lib / row[0]
    ct_map = {
        "epub": "application/epub+zip",
        "mobi": "application/x-mobipocket-ebook",
        "pdf": "application/pdf",
    }
    for fmt in ("epub", "mobi", "pdf"):
        matches = list(book_dir.glob(f"*.{fmt}"))
        if matches:
            return matches[0].read_bytes(), ct_map[fmt]

    raise FileNotFoundError(
        f"No supported format (epub/mobi/pdf) found for book {book_id} in {book_dir}"
    )


def _resolve_blob(uri: str, cache: _BlobCache) -> FetchResult:
    """blob://sha256:<hex> → FetchResult from cache (no network fetch)."""
    # Strip scheme: blob://sha256:abcdef... or blob://abcdef...
    rest = uri[len("blob://") :]
    if rest.startswith("sha256:"):
        sha256_hex = rest[len("sha256:") :]
    else:
        sha256_hex = rest

    entry = cache.get_entry(sha256_hex)
    if entry is None:
        raise KeyError(f"blob not in cache: {sha256_hex}")

    raw = entry.blob_bin_path.read_bytes()
    return FetchResult(
        uri=uri,
        content=_text_from_raw(raw, entry.content_type),
        sha256=sha256_hex,
        content_type=entry.content_type,
        size_bytes=entry.size_bytes,
        from_cache=True,
        blob_path=entry.blob_bin_path,
        fetched_at=entry.fetched_at,
    )


# ── Public API ─────────────────────────────────────────────────────────────────


def fetch_uri(
    uri: str,
    *,
    force_refresh: bool = False,
    cache_root: Path | None = None,
    _calibre_library: Path | None = None,
) -> FetchResult:
    """Fetch and cache content for any supported URI scheme.

    Args:
        uri: Any supported URI (https://, http://, file://, calibre://, blob://).
        force_refresh: When True, ignore cache and re-fetch even if blob exists.
        cache_root: Override blob cache root (test injection).
        _calibre_library: Override Calibre library path (test injection).

    Returns:
        FetchResult with content (text), sha256, and provenance.
    """
    cache = _BlobCache(root=cache_root)
    scheme = urllib.parse.urlparse(uri).scheme.lower()

    if scheme == "blob":
        return _resolve_blob(uri, cache)

    # For other schemes: check cache by URI first (if not force_refresh)
    # Cache lookup by URI requires scanning metadata — skip for v1;
    # callers can pass blob://sha256:<hex> if they have the digest.
    # Fetch fresh, then put (put() is idempotent on same bytes).

    fetched_at = _now_iso()

    if scheme in ("https", "http"):
        raw, content_type = _fetch_http(uri)
    elif scheme == "file":
        raw, content_type = _fetch_file(uri)
    elif scheme == "calibre":
        raw, content_type = _fetch_calibre(uri, library_path=_calibre_library)
    else:
        raise ValueError(f"Unsupported URI scheme: {scheme!r} in {uri!r}")

    entry = cache.put(
        raw, content_type=content_type, source_uri=uri, fetched_at=fetched_at
    )

    return FetchResult(
        uri=uri,
        content=_text_from_raw(raw, content_type),
        sha256=entry.sha256,
        content_type=content_type,
        size_bytes=entry.size_bytes,
        from_cache=False,
        blob_path=entry.blob_bin_path,
        fetched_at=fetched_at,
    )

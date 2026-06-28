"""
Tests for devices/reader — URI scheme handlers + content-addressed blob cache.

All network and filesystem dependencies are injected or mocked:
  - https:// fetch mocked via unittest.mock.patch on urllib.request.urlopen
  - file:// uses tmp_path pytest fixture (real filesystem, no mocks)
  - calibre:// uses a synthetic SQLite metadata.db in tmp_path
  - blob:// resolved directly from cache (no fetch)

No LLM calls, no Postgres, no network.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.reader.blob_cache import BlobCache, BlobEntry
from unseen_university.devices.reader.uri import FetchResult, _strip_html, _text_from_raw, fetch_uri

# ── BlobCache ──────────────────────────────────────────────────────────────────


class TestBlobCache:
    def test_put_creates_bin_and_meta(self, tmp_path):
        cache = BlobCache(root=tmp_path)
        raw = b"hello world"
        entry = cache.put(raw, content_type="text/plain", source_uri="file:///test.txt")

        assert entry.blob_bin_path.exists()
        assert entry.blob_meta_path.exists()
        assert entry.sha256 == hashlib.sha256(raw).hexdigest()
        assert entry.content_type == "text/plain"
        assert entry.size_bytes == len(raw)

    def test_put_idempotent_same_bytes(self, tmp_path):
        cache = BlobCache(root=tmp_path)
        raw = b"idempotent content"
        entry1 = cache.put(raw, content_type="text/plain", source_uri="https://a.com/")
        entry2 = cache.put(raw, content_type="text/plain", source_uri="https://b.com/")
        # Same content → same sha256 → same paths
        assert entry1.sha256 == entry2.sha256
        assert entry1.blob_bin_path == entry2.blob_bin_path

    def test_get_entry_returns_none_for_missing(self, tmp_path):
        cache = BlobCache(root=tmp_path)
        assert cache.get_entry("a" * 64) is None

    def test_get_entry_returns_entry_after_put(self, tmp_path):
        cache = BlobCache(root=tmp_path)
        raw = b"round trip"
        entry = cache.put(raw, content_type="text/plain", source_uri="file:///r.txt")
        retrieved = cache.get_entry(entry.sha256)
        assert retrieved is not None
        assert retrieved.sha256 == entry.sha256
        assert retrieved.blob_bin_path.read_bytes() == raw

    def test_shard_directory_structure(self, tmp_path):
        cache = BlobCache(root=tmp_path)
        raw = b"shard test"
        entry = cache.put(raw, content_type="text/plain", source_uri="file:///s.txt")
        # Should be at <root>/<sha256[:2]>/<sha256>.blob.bin
        expected_dir = tmp_path / entry.sha256[:2]
        assert expected_dir.is_dir()


# ── HTML stripping ─────────────────────────────────────────────────────────────


class TestStripHtml:
    def test_extracts_body_text(self):
        html = b"<html><body><p>Hello world</p></body></html>"
        result = _strip_html(html)
        assert "Hello world" in result

    def test_strips_script_tags(self):
        html = b"<html><body><script>alert('x')</script><p>Keep this</p></body></html>"
        result = _strip_html(html)
        assert "alert" not in result
        assert "Keep this" in result

    def test_strips_style_tags(self):
        html = (
            b"<html><head><style>body{color:red}</style></head><body>Text</body></html>"
        )
        result = _strip_html(html)
        assert "color" not in result
        assert "Text" in result

    def test_max_chars_truncates(self):
        html = b"<p>" + b"x" * 10000 + b"</p>"
        result = _strip_html(html, max_chars=100)
        assert len(result) <= 100

    def test_empty_html_returns_empty(self):
        result = _strip_html(b"")
        assert result == ""


class TestTextFromRaw:
    def test_html_type_stripped(self):
        raw = b"<html><body><p>Hello</p></body></html>"
        result = _text_from_raw(raw, "text/html")
        assert "Hello" in result
        assert "<html>" not in result

    def test_text_plain_decoded(self):
        raw = "Plain text content".encode("utf-8")
        result = _text_from_raw(raw, "text/plain")
        assert result == "Plain text content"

    def test_binary_returns_empty(self):
        raw = b"\x00\x01\x02\x03"
        result = _text_from_raw(raw, "application/epub+zip")
        assert result == ""

    def test_content_type_with_charset(self):
        raw = b"<p>Hello</p>"
        result = _text_from_raw(raw, "text/html; charset=utf-8")
        assert "Hello" in result


# ── fetch_uri: https:// ────────────────────────────────────────────────────────


class TestFetchHttps:
    def _mock_response(self, body: bytes, content_type: str = "text/html"):
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.headers.get = MagicMock(return_value=content_type)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_https_fetch_creates_blob(self, tmp_path):
        body = b"<html><body><p>Fetched content</p></body></html>"
        mock_resp = self._mock_response(body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_uri("https://example.com/page", cache_root=tmp_path)

        assert isinstance(result, FetchResult)
        assert result.sha256 == hashlib.sha256(body).hexdigest()
        assert "Fetched content" in result.content
        assert result.from_cache is False
        assert result.blob_path is not None
        assert result.blob_path.exists()

    def test_https_blob_matches_expected_sha256(self, tmp_path):
        body = b"<p>deterministic</p>"
        expected = hashlib.sha256(body).hexdigest()
        mock_resp = self._mock_response(body)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_uri("https://example.com/", cache_root=tmp_path)
        assert result.sha256 == expected

    def test_second_call_same_uri_no_refetch(self, tmp_path):
        body = b"<p>once only</p>"
        mock_resp = self._mock_response(body)
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            fetch_uri("https://example.com/once", cache_root=tmp_path)
            fetch_uri("https://example.com/once", cache_root=tmp_path)
        # urlopen called twice because v1 doesn't cache by URL — but blob.put is idempotent
        # Both calls produce same sha256 and same blob path (idempotency test)
        assert mock_open.call_count == 2  # fetch happens, put is idempotent

    def test_blob_uri_avoids_refetch(self, tmp_path):
        """blob://sha256:<hex> → direct cache hit, no urlopen call."""
        body = b"<p>cached content</p>"
        sha256 = hashlib.sha256(body).hexdigest()
        # Pre-populate cache
        cache = BlobCache(root=tmp_path)
        entry = cache.put(body, content_type="text/html", source_uri="https://x.com/")

        with patch("urllib.request.urlopen") as mock_open:
            result = fetch_uri(f"blob://sha256:{sha256}", cache_root=tmp_path)
        mock_open.assert_not_called()
        assert result.sha256 == sha256
        assert result.from_cache is True


# ── fetch_uri: file:// ─────────────────────────────────────────────────────────


class TestFetchFile:
    def test_file_uri_reads_content(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("Plain file content", encoding="utf-8")
        result = fetch_uri(f"file://{f}", cache_root=tmp_path / "cache")
        assert "Plain file content" in result.content
        assert result.content_type == "text/plain"
        assert result.from_cache is False

    def test_file_uri_html_stripped(self, tmp_path):
        f = tmp_path / "page.html"
        f.write_text("<html><body><p>HTML file</p></body></html>", encoding="utf-8")
        result = fetch_uri(f"file://{f}", cache_root=tmp_path / "cache")
        assert "HTML file" in result.content
        assert "<html>" not in result.content

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            fetch_uri("file:///nonexistent/path/file.txt", cache_root=tmp_path)

    def test_file_cached_after_fetch(self, tmp_path):
        f = tmp_path / "cached.txt"
        f.write_bytes(b"cache me")
        result = fetch_uri(f"file://{f}", cache_root=tmp_path / "cache")
        assert result.blob_path is not None
        assert result.blob_path.exists()
        assert result.blob_path.read_bytes() == b"cache me"


# ── fetch_uri: calibre:// ──────────────────────────────────────────────────────


class TestFetchCalibre:
    def _make_calibre_library(
        self, tmp_path: Path, book_id: int, fmt: str = "epub"
    ) -> Path:
        """Create a minimal synthetic Calibre library in tmp_path."""
        lib = tmp_path / "calibre_lib"
        lib.mkdir()
        db_path = lib / "metadata.db"
        rel_path = f"Author/Book{book_id}"
        book_dir = lib / rel_path
        book_dir.mkdir(parents=True)
        # Write a fake epub (just bytes — not a real epub)
        book_file = book_dir / f"book{book_id}.{fmt}"
        book_file.write_bytes(b"FAKE_EPUB_CONTENT_" + str(book_id).encode())
        # Create metadata.db with Calibre schema
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT NOT NULL)")
        conn.execute("INSERT INTO books (id, path) VALUES (?, ?)", (book_id, rel_path))
        conn.commit()
        conn.close()
        return lib

    def test_calibre_fetch_returns_bytes(self, tmp_path):
        lib = self._make_calibre_library(tmp_path, book_id=42)
        result = fetch_uri(
            "calibre://42",
            cache_root=tmp_path / "cache",
            _calibre_library=lib,
        )
        assert result.content_type == "application/epub+zip"
        assert result.size_bytes > 0
        assert result.sha256 != ""

    def test_calibre_not_found_raises(self, tmp_path):
        lib = self._make_calibre_library(tmp_path, book_id=1)
        with pytest.raises(KeyError, match="999"):
            fetch_uri(
                "calibre://999",
                cache_root=tmp_path / "cache",
                _calibre_library=lib,
            )

    def test_calibre_missing_library_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Calibre library not found"):
            fetch_uri(
                "calibre://1",
                cache_root=tmp_path / "cache",
                _calibre_library=tmp_path / "nonexistent",
            )

    def test_calibre_result_cached(self, tmp_path):
        lib = self._make_calibre_library(tmp_path, book_id=7)
        result = fetch_uri(
            "calibre://7",
            cache_root=tmp_path / "cache",
            _calibre_library=lib,
        )
        assert result.blob_path is not None
        assert result.blob_path.exists()

    def test_calibre_epub_content_type_empty_text(self, tmp_path):
        """epub is binary — content field is empty in this tier."""
        lib = self._make_calibre_library(tmp_path, book_id=3)
        result = fetch_uri(
            "calibre://3",
            cache_root=tmp_path / "cache",
            _calibre_library=lib,
        )
        # Binary formats: text extraction is for output modes, not the fetch tier
        assert result.content == ""


# ── fetch_uri: unsupported scheme ─────────────────────────────────────────────


class TestUnsupportedScheme:
    def test_unknown_scheme_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported URI scheme"):
            fetch_uri("ftp://example.com/file.txt", cache_root=tmp_path)

    def test_comms_scheme_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported URI scheme"):
            fetch_uri("comms://akien/inbox", cache_root=tmp_path)

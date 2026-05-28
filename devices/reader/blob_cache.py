"""
blob_cache.py — content-addressed local blob store for ReaderDevice.

Stores raw fetched bytes keyed by SHA-256 of content:
  ~/.unseen_university/blobs/<sha256[:2]>/<sha256>.blob.bin   — raw bytes
  ~/.unseen_university/blobs/<sha256[:2]>/<sha256>.blob.json  — metadata

Idempotent: calling put() twice with identical bytes is a no-op after the
first write (content-addressed = same sha256 = same path, already exists).
Multi-instance safe: filesystem rename-atomicity guards concurrent writes.
No DB required, no eviction policy in v1.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_RUNTIME_ROOT = Path(
    os.environ.get("ADC_RUNTIME_ROOT", Path.home() / ".unseen_university")
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BlobEntry:
    sha256: str
    content_type: str
    size_bytes: int
    source_uri: str
    fetched_at: str
    blob_bin_path: Path
    blob_meta_path: Path


class BlobCache:
    """Content-addressed local blob cache — fetch once, reuse forever."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (_RUNTIME_ROOT / "blobs")

    def _paths(self, sha256: str) -> tuple[Path, Path]:
        shard = sha256[:2]
        base = self.root / shard / sha256
        return Path(str(base) + ".blob.bin"), Path(str(base) + ".blob.json")

    def get_entry(self, sha256: str) -> BlobEntry | None:
        """Return BlobEntry if sha256 is cached, else None."""
        bin_path, meta_path = self._paths(sha256)
        if not bin_path.exists() or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return BlobEntry(
            sha256=sha256,
            content_type=meta.get("content_type", ""),
            size_bytes=meta.get("size_bytes", 0),
            source_uri=meta.get("source_uri", ""),
            fetched_at=meta.get("fetched_at", ""),
            blob_bin_path=bin_path,
            blob_meta_path=meta_path,
        )

    def put(
        self,
        raw_bytes: bytes,
        *,
        content_type: str,
        source_uri: str,
        fetched_at: str | None = None,
    ) -> BlobEntry:
        """Store raw bytes; return BlobEntry (idempotent — no-op if already cached)."""
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        bin_path, meta_path = self._paths(sha256)

        if not bin_path.exists():
            bin_path.parent.mkdir(parents=True, exist_ok=True)
            ts = fetched_at or _now_iso()
            # Write bin atomically via temp file + rename
            tmp_bin = Path(tempfile.mktemp(dir=bin_path.parent, suffix=".tmp"))
            try:
                tmp_bin.write_bytes(raw_bytes)
                tmp_bin.rename(bin_path)
            except Exception:
                tmp_bin.unlink(missing_ok=True)
                raise

            meta = {
                "sha256": sha256,
                "content_type": content_type,
                "size_bytes": len(raw_bytes),
                "source_uri": source_uri,
                "fetched_at": ts,
            }
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        entry = self.get_entry(sha256)
        assert (
            entry is not None
        ), f"put succeeded but get_entry returned None for {sha256}"
        return entry

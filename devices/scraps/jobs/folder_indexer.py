"""
folder_indexer.py — Incremental full-text indexer for configured folder paths.

Reads paths from config/librarian.yaml (indexer_paths list), chunks each file,
stores in adc.search_index (Postgres). Only re-indexes files with a newer mtime
than the last indexed record. GIN index on chunk text enables fast FTS queries.

Run as a Scraps periodic job or directly:
    python -m devices.scraps.jobs.folder_indexer
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
)
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "librarian.yaml"
_CHUNK_SIZE = int(os.environ.get("LIBRARIAN_CHUNK_SIZE", "800"))
_MAX_FILES = int(os.environ.get("LIBRARIAN_INDEX_MAX_FILES", "5000"))

_INDEXABLE_SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".rst", ".toml"}


def _load_indexer_paths() -> list[str]:
    try:
        import yaml
        cfg = yaml.safe_load(_CONFIG_PATH.read_text()) or {}
        return cfg.get("indexer_paths", [])
    except Exception as exc:
        log.warning("folder_indexer: config load failed: %s", exc)
        return []


def _chunk_file(path: Path, chunk_size: int = _CHUNK_SIZE) -> Iterator[tuple[int, str]]:
    """Yield (chunk_index, chunk_text) tuples for the given file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        log.debug("folder_indexer: read failed %s: %s", path, exc)
        return
    for i, start in enumerate(range(0, len(text), chunk_size)):
        chunk = text[start:start + chunk_size].strip()
        if chunk:
            yield i, chunk


def _get_indexed_mtimes(conn, paths: list[str]) -> dict[str, float]:
    """Return {path: max_file_mtime} for already-indexed paths."""
    if not paths:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path, MAX(file_mtime) FROM adc.search_index WHERE path = ANY(%s) GROUP BY path",
                (paths,),
            )
            return {row[0]: float(row[1]) for row in cur.fetchall()}
    except Exception as exc:
        log.warning("folder_indexer: mtime query failed: %s", exc)
        return {}


def _upsert_chunks(conn, path: str, mtime: float, chunks: list[tuple[int, str]]) -> int:
    """Delete existing chunks for path and insert new ones."""
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adc.search_index WHERE path = %s", (path,))
            for idx, text in chunks:
                cur.execute(
                    """INSERT INTO adc.search_index (path, chunk_index, chunk_text, file_mtime)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (path, chunk_index) DO UPDATE
                           SET chunk_text = EXCLUDED.chunk_text,
                               file_mtime = EXCLUDED.file_mtime,
                               indexed_at = now()""",
                    (path, idx, text, mtime),
                )
        return len(chunks)
    except Exception as exc:
        log.warning("folder_indexer: upsert failed for %s: %s", path, exc)
        return 0


def run_indexer(paths: list[str] | None = None) -> dict:
    """Run one indexer pass over the configured (or supplied) paths.

    Returns {"indexed": N, "skipped": N, "errors": N}.
    """
    if paths is None:
        paths = _load_indexer_paths()
    if not paths:
        log.debug("folder_indexer: no paths configured")
        return {"indexed": 0, "skipped": 0, "errors": 0}

    try:
        import psycopg2
        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
    except Exception as exc:
        log.warning("folder_indexer: DB connect failed: %s", exc)
        return {"indexed": 0, "skipped": 0, "errors": 1}

    stats = {"indexed": 0, "skipped": 0, "errors": 0}
    files_seen = 0

    try:
        # Collect all candidate files
        candidate_files: list[Path] = []
        for base in paths:
            base_path = Path(base).expanduser()
            if not base_path.exists():
                log.debug("folder_indexer: path not found: %s", base)
                continue
            if base_path.is_file():
                candidate_files.append(base_path)
            else:
                for p in base_path.rglob("*"):
                    if p.is_file() and p.suffix.lower() in _INDEXABLE_SUFFIXES:
                        candidate_files.append(p)
                    if len(candidate_files) >= _MAX_FILES:
                        break

        # Batch mtime lookup
        str_paths = [str(p) for p in candidate_files]
        indexed_mtimes = _get_indexed_mtimes(conn, str_paths)

        with conn:
            for file_path in candidate_files:
                str_p = str(file_path)
                try:
                    mtime = file_path.stat().st_mtime
                except Exception:
                    stats["errors"] += 1
                    continue

                known_mtime = indexed_mtimes.get(str_p, 0.0)
                if mtime <= known_mtime:
                    stats["skipped"] += 1
                    continue

                chunks = list(_chunk_file(file_path))
                n = _upsert_chunks(conn, str_p, mtime, chunks)
                if n > 0:
                    stats["indexed"] += 1
                    log.debug("folder_indexer: indexed %s (%d chunks)", str_p, n)
                files_seen += 1

    finally:
        conn.close()

    log.info(
        "folder_indexer: done — indexed=%d skipped=%d errors=%d",
        stats["indexed"], stats["skipped"], stats["errors"],
    )
    return stats


def search_indexed(query: str, limit: int = 10) -> list[dict]:
    """Search adc.search_index using Postgres full-text search."""
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(_DB_URL, connect_timeout=5)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT path, chunk_index, chunk_text,
                          ts_rank(to_tsvector('english', chunk_text),
                                  plainto_tsquery('english', %s)) AS rank
                   FROM adc.search_index
                   WHERE to_tsvector('english', chunk_text) @@ plainto_tsquery('english', %s)
                   ORDER BY rank DESC
                   LIMIT %s""",
                (query, query, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        log.warning("search_indexed: query failed: %s", exc)
        return []


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_indexer()
    print(result)

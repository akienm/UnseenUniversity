"""global_kb.py — Global Knowledge Base bootstrap and import tooling.

Implements `uu bootstrap --global-kb <url>` flow:
  1. Clone (or pull) the KB repo to ~/.unseen_university/global-kb/
  2. Read all patterns/*.jsonl files
  3. Upsert into global_kb.patterns table (Postgres)

No credentials, no instance-specific data ever enters the global repo.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

_DEFAULT_KB_URL = "https://github.com/akienm/unseen-university-kb"
_LOCAL_KB_DIR_NAME = "global-kb"


def _kb_local_dir() -> Path:
    import os

    root = Path(os.environ.get("IGOR_RUNTIME_ROOT", Path.home() / ".unseen_university"))
    return root / _LOCAL_KB_DIR_NAME


def clone_or_update(url: str | None = None) -> Path:
    """Clone the global KB repo (or pull if already present). Returns local path."""
    url = url or _DEFAULT_KB_URL
    local = _kb_local_dir()

    if (local / ".git").exists():
        log.info("global_kb: pulling updates from %s", url)
        result = subprocess.run(
            ["git", "-C", str(local), "pull", "--rebase", "--quiet"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log.warning("global_kb: git pull failed: %s", result.stderr[:300])
    else:
        log.info("global_kb: cloning %s → %s", url, local)
        local.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--quiet", url, str(local)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"global_kb: git clone failed: {result.stderr[:300]}")

    return local


def load_local_kb(local_dir: Path | None = None) -> Path:
    """Use a local directory (no git clone needed). Returns the dir."""
    local = local_dir or _kb_local_dir()
    if not local.exists():
        raise FileNotFoundError(f"global_kb: local dir not found: {local}")
    return local


def _iter_records(kb_dir: Path) -> Iterator[dict]:
    """Yield all JSONL records from patterns/ subdirectory."""
    patterns_dir = kb_dir / "patterns"
    if not patterns_dir.exists():
        log.warning("global_kb: no patterns/ directory in %s", kb_dir)
        return
    for jsonl_file in sorted(patterns_dir.glob("*.jsonl")):
        log.debug("global_kb: reading %s", jsonl_file)
        for lineno, line in enumerate(jsonl_file.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                log.warning("global_kb: %s line %d: JSON error: %s", jsonl_file.name, lineno, e)


def _ensure_schema(conn) -> None:
    """Create global_kb.patterns table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS global_kb")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS global_kb.patterns (
                id          TEXT PRIMARY KEY,
                title       TEXT,
                type        TEXT,
                tags        JSONB,
                content     TEXT,
                version     TEXT,
                source      TEXT,
                imported_at TIMESTAMPTZ DEFAULT now()
            )
        """)
    conn.commit()


def import_records(kb_dir: Path, db_url: str) -> int:
    """Import all JSONL records from kb_dir into Postgres. Returns count upserted."""
    import psycopg2

    conn = psycopg2.connect(db_url, connect_timeout=10)
    _ensure_schema(conn)
    count = 0
    try:
        with conn.cursor() as cur:
            for rec in _iter_records(kb_dir):
                if not rec.get("id"):
                    continue
                cur.execute("""
                    INSERT INTO global_kb.patterns (id, title, type, tags, content, version, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                      SET title = EXCLUDED.title,
                          type = EXCLUDED.type,
                          tags = EXCLUDED.tags,
                          content = EXCLUDED.content,
                          version = EXCLUDED.version,
                          source = EXCLUDED.source,
                          imported_at = now()
                """, (
                    rec["id"],
                    rec.get("title", ""),
                    rec.get("type", "pattern"),
                    json.dumps(rec.get("tags", [])),
                    rec.get("content", ""),
                    rec.get("version", "1.0"),
                    rec.get("source", "unknown"),
                ))
                count += 1
        conn.commit()
    finally:
        conn.close()
    log.info("global_kb: upserted %d records", count)
    return count


def bootstrap(url: str | None = None, local_dir: Path | None = None, db_url: str | None = None) -> int:
    """Full bootstrap: clone/pull KB, import records. Returns count imported."""
    if local_dir:
        kb_dir = load_local_kb(local_dir)
    else:
        kb_dir = clone_or_update(url)

    if db_url is None:
        from unseen_university.devices.igor.paths import paths
        db_url = paths().home_db_url

    return import_records(kb_dir, db_url)

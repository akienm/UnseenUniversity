#!/usr/bin/env python3
"""m_vault_source_path.py — Add source_path column to vault.credentials.

source_path stores the canonical origin/destination for this credential:
  <absolute_file_path>:<key_name>
e.g. /home/akien/.unseen_university/akien/akien.credentials.cfg:OLLAMA_API_KEY

Used by:
  - seed.py: populated on import so provenance is recorded
  - export: grouped by file path, written back as KEY=value lines
  - UI: shown as editable column; blank for manually-entered credentials

Idempotent.
"""

from __future__ import annotations
import logging, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import psycopg2

log = logging.getLogger(__name__)
_DB_URL = os.environ.get("UU_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")

_ADD_COLUMN = """
ALTER TABLE vault.credentials
  ADD COLUMN IF NOT EXISTS source_path TEXT NOT NULL DEFAULT '';
"""
_ADD_INDEX = "CREATE INDEX IF NOT EXISTS vault_creds_source_path ON vault.credentials (source_path);"

def migrate() -> None:
    conn = psycopg2.connect(_DB_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_ADD_COLUMN)
            cur.execute(_ADD_INDEX)
        log.info("vault_source_path migration complete")
    finally:
        conn.close()

def verify() -> bool:
    try:
        conn = psycopg2.connect(_DB_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='vault' AND table_name='credentials' AND column_name='source_path';"
            )
            ok = cur.fetchone() is not None
        conn.close()
        if not ok:
            log.error("source_path column missing")
        return ok
    except Exception as exc:
        log.error("verify failed: %s", exc)
        return False

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    migrate()
    sys.exit(0 if verify() else 1)

if __name__ == "__main__":
    main()

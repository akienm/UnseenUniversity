#!/usr/bin/env python3
"""search_index_bootstrap.py — Create the adc.search_index table.

Usage:
    python3 scripts/search_index_bootstrap.py              # migrate only (idempotent)
    python3 scripts/search_index_bootstrap.py --rollback   # DROP TABLE (destructive)
"""

from __future__ import annotations

import argparse
import os

import psycopg2

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS adc;"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS adc.search_index (
    id          BIGSERIAL PRIMARY KEY,
    path        TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_text  TEXT NOT NULL,
    file_mtime  DOUBLE PRECISION NOT NULL DEFAULT 0,
    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (path, chunk_index)
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS search_index_fts_gin ON adc.search_index USING GIN (to_tsvector('english', chunk_text));",
    "CREATE INDEX IF NOT EXISTS search_index_path ON adc.search_index (path);",
    "CREATE INDEX IF NOT EXISTS search_index_mtime ON adc.search_index (file_mtime);",
]

_DROP_TABLE = "DROP TABLE IF EXISTS adc.search_index CASCADE;"


def migrate(pg_url: str = _PG_URL) -> None:
    conn = psycopg2.connect(pg_url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_SCHEMA)
                cur.execute(_CREATE_TABLE)
                for idx in _CREATE_INDEXES:
                    cur.execute(idx)
        print("adc.search_index: migrated OK")
    finally:
        conn.close()


def rollback(pg_url: str = _PG_URL) -> None:
    conn = psycopg2.connect(pg_url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_DROP_TABLE)
        print("adc.search_index: dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if args.rollback:
        rollback()
    else:
        migrate()

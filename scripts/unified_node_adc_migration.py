#!/usr/bin/env python3
"""Apply tags+triggers columns to adc.* tables. Idempotent — safe to re-run.

Part of T-unified-node-rollout. Handles adc.* tables separately from
cortex.py _SCHEMA_MIGRATIONS (which owns clan.* and infra.*).
"""
import os
from unseen_university.identity import home_db_url
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed — run: pip install psycopg2-binary")
    sys.exit(1)

MIGRATIONS = [
    ("adc.palace tags",
     "ALTER TABLE adc.palace ADD COLUMN IF NOT EXISTS tags jsonb DEFAULT '[]'::jsonb"),
    ("adc.palace triggers",
     "ALTER TABLE adc.palace ADD COLUMN IF NOT EXISTS triggers jsonb DEFAULT '{}'::jsonb"),
    ("adc.palace tags GIN",
     "CREATE INDEX IF NOT EXISTS idx_palace_tags_gin ON adc.palace USING GIN (tags)"),
    ("adc.eval_history tags",
     "ALTER TABLE adc.eval_history ADD COLUMN IF NOT EXISTS tags jsonb DEFAULT '[]'::jsonb"),
    ("adc.eval_history triggers",
     "ALTER TABLE adc.eval_history ADD COLUMN IF NOT EXISTS triggers jsonb DEFAULT '{}'::jsonb"),
    ("adc.eval_history tags GIN",
     "CREATE INDEX IF NOT EXISTS idx_eval_history_tags_gin ON adc.eval_history USING GIN (tags)"),
]


def main():
    conn = psycopg2.connect(home_db_url(), connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()
    applied = 0
    for name, sql in MIGRATIONS:
        try:
            cur.execute(sql)
            print(f"  OK: {name}")
            applied += 1
        except Exception as exc:
            print(f"  SKIP: {name} ({exc})")
    conn.close()
    print(f"\nDone: {applied}/{len(MIGRATIONS)} applied")


if __name__ == "__main__":
    main()

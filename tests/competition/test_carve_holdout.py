"""
Tests for T-competition-holdout-split: carve_holdout.py

Verifies stratified 20% holdout marking on competition.memories.
"""
import os
import sys
import unittest
import uuid
from pathlib import Path

import psycopg2

_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
)

_REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "lab"))

from devlab.competition.carve_holdout import carve  # noqa: E402


def _conn():
    return psycopg2.connect(_DB_URL)


def _insert_test_rows(memory_type: str, count: int, prefix: str) -> list[str]:
    ids = [f"__test_{prefix}_{i:04d}__" for i in range(count)]
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                for i, mid in enumerate(ids):
                    cur.execute(
                        "INSERT INTO competition.memories (id, narrative, memory_type, holdout) "
                        "VALUES (%s, %s, %s, false) ON CONFLICT (id) DO NOTHING",
                        (mid, f"test narrative {i}", memory_type),
                    )
    finally:
        conn.close()
    return ids


def _delete_test_rows(ids: list[str]) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "DELETE FROM competition.memories WHERE id = %s",
                    [(mid,) for mid in ids],
                )
    finally:
        conn.close()


class TestCarveHoldout(unittest.TestCase):
    def setUp(self):
        prefix = uuid.uuid4().hex[:8]
        # Insert 10 FACTUAL, 10 EPISODIC (two types for stratification)
        self.factual_ids = _insert_test_rows("FACTUAL", 10, f"fact_{prefix}")
        self.episodic_ids = _insert_test_rows("EPISODIC", 10, f"ep_{prefix}")
        self.all_ids = self.factual_ids + self.episodic_ids

    def tearDown(self):
        _delete_test_rows(self.all_ids)

    def test_holdout_column_exists(self):
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'competition' AND table_name = 'memories' "
                "AND column_name = 'holdout'"
            )
            self.assertIsNotNone(cur.fetchone(), "holdout column must exist")
        conn.close()

    def test_stratified_split_approximately_twenty_percent(self):
        results = carve(dry_run=False)
        for mtype in ("FACTUAL", "EPISODIC"):
            if mtype not in results:
                continue
            stats = results[mtype]
            # Holdout fraction should be ~20% regardless of absolute row count.
            # Allow 10%–30% tolerance to handle rounding on small sets.
            holdout_ratio = stats["holdout"] / stats["total"]
            self.assertGreaterEqual(
                holdout_ratio, 0.10,
                f"{mtype}: holdout ratio {holdout_ratio:.2f} below 10%",
            )
            self.assertLessEqual(
                holdout_ratio, 0.30,
                f"{mtype}: holdout ratio {holdout_ratio:.2f} above 30%",
            )

    def test_dry_run_does_not_modify(self):
        # First reset all to false
        conn = _conn()
        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE competition.memories SET holdout = false WHERE id = %s",
                    [(mid,) for mid in self.all_ids],
                )
        conn.close()

        carve(dry_run=True)

        # All should still be false
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM competition.memories "
                "WHERE id = ANY(%s) AND holdout = true",
                (self.all_ids,),
            )
            holdout_count = cur.fetchone()[0]
        conn.close()
        self.assertEqual(holdout_count, 0, "dry_run must not write any holdout marks")


if __name__ == "__main__":
    unittest.main()

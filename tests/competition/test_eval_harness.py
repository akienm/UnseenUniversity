"""
Tests for T-competition-eval-harness: eval_harness.py

Verifies:
- run_race() returns correct scorecard structure
- accuracy computed correctly from mock classifiers
- cloud_calls summed correctly
- handles empty holdout gracefully
- scorecard is deterministic
"""
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg2

_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "lab"))

from lab.competition.eval_harness import _fetch_holdout, _run_classifier, run_race  # noqa: E402


def _conn():
    return psycopg2.connect(_DB_URL)


def _insert_holdout(mem_id: str, narrative: str, mtype: str) -> None:
    conn = _conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO competition.memories (id, narrative, memory_type, holdout) "
                "VALUES (%s, %s, %s, true) ON CONFLICT (id) DO NOTHING",
                (mem_id, narrative, mtype),
            )
    conn.close()


def _delete(mem_ids: list[str]) -> None:
    conn = _conn()
    with conn:
        with conn.cursor() as cur:
            cur.executemany(
                "DELETE FROM competition.memories WHERE id = %s",
                [(mid,) for mid in mem_ids],
            )
    conn.close()


class TestRunClassifier(unittest.TestCase):
    """Test _run_classifier directly with synthetic data."""

    def _make_rows(self, types):
        return [(f"id_{i}", f"narrative {i}", t) for i, t in enumerate(types)]

    def test_perfect_accuracy(self):
        rows = self._make_rows(["FACTUAL", "PROCEDURAL", "EPISODIC"])
        perfect_fn = lambda text: ("FACTUAL", 0) if "0" in text else \
                                  ("PROCEDURAL", 0) if "1" in text else ("EPISODIC", 0)
        result = _run_classifier(perfect_fn, rows, "test")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["correct"], 3)
        self.assertEqual(result["accuracy_pct"], 100.0)

    def test_zero_accuracy(self):
        rows = self._make_rows(["FACTUAL", "FACTUAL"])
        wrong_fn = lambda text: ("EPISODIC", 1)
        result = _run_classifier(wrong_fn, rows, "test")
        self.assertEqual(result["correct"], 0)
        self.assertEqual(result["accuracy_pct"], 0.0)

    def test_cloud_calls_summed(self):
        rows = self._make_rows(["FACTUAL", "FACTUAL", "FACTUAL"])
        fn = lambda text: ("FACTUAL", 2)  # 2 cloud calls each
        result = _run_classifier(fn, rows, "test")
        self.assertEqual(result["total_cloud_calls"], 6)

    def test_partial_accuracy(self):
        rows = self._make_rows(["FACTUAL", "FACTUAL", "FACTUAL", "FACTUAL"])
        # Correct 2/4 = 50%
        count = [0]
        def fn(text):
            count[0] += 1
            return ("FACTUAL", 0) if count[0] <= 2 else ("EPISODIC", 0)
        result = _run_classifier(fn, rows, "test")
        self.assertEqual(result["accuracy_pct"], 50.0)


class TestRunRaceWithDb(unittest.TestCase):
    def setUp(self):
        prefix = uuid.uuid4().hex[:8]
        self.mem_ids = []
        data = [
            (f"__eval_f_{prefix}__", "a factual statement", "FACTUAL"),
            (f"__eval_p_{prefix}__", "step by step instructions", "PROCEDURAL"),
        ]
        for mid, narr, mtype in data:
            _insert_holdout(mid, narr, mtype)
            self.mem_ids.append(mid)

    def tearDown(self):
        _delete(self.mem_ids)

    def test_run_race_returns_scorecard_structure(self):
        knn_fn = lambda text: ("FACTUAL", 0)
        llm_fn = lambda text: ("FACTUAL", 1)

        with patch("lab.competition.classifiers.knn_classifier.classify", side_effect=knn_fn), \
             patch("lab.competition.classifiers.llm_classifier.classify", side_effect=llm_fn):
            result = run_race()

        self.assertIn("holdout_rows", result)
        self.assertIn("knn", result)
        self.assertIn("llm", result)
        self.assertGreaterEqual(result["holdout_rows"], 2)

        for key in ("name", "total", "correct", "accuracy_pct", "total_cloud_calls"):
            self.assertIn(key, result["knn"])
            self.assertIn(key, result["llm"])

    def test_run_race_deterministic(self):
        knn_fn = lambda text: ("FACTUAL", 0)
        llm_fn = lambda text: ("FACTUAL", 1)

        with patch("lab.competition.classifiers.knn_classifier.classify", side_effect=knn_fn), \
             patch("lab.competition.classifiers.llm_classifier.classify", side_effect=llm_fn):
            r1 = run_race()

        # Reset side_effect call counters by re-patching
        with patch("lab.competition.classifiers.knn_classifier.classify", side_effect=knn_fn), \
             patch("lab.competition.classifiers.llm_classifier.classify", side_effect=llm_fn):
            r2 = run_race()

        self.assertEqual(r1["knn"]["accuracy_pct"], r2["knn"]["accuracy_pct"])
        self.assertEqual(r1["llm"]["total_cloud_calls"], r2["llm"]["total_cloud_calls"])

    def test_cloud_calls_tracked_per_classifier(self):
        knn_fn = lambda text: ("FACTUAL", 0)  # no cloud calls
        llm_fn = lambda text: ("FACTUAL", 1)  # 1 cloud call each

        with patch("lab.competition.classifiers.knn_classifier.classify", side_effect=knn_fn), \
             patch("lab.competition.classifiers.llm_classifier.classify", side_effect=llm_fn):
            result = run_race()

        n = result["holdout_rows"]
        self.assertEqual(result["knn"]["total_cloud_calls"], 0)
        self.assertEqual(result["llm"]["total_cloud_calls"], n)


class TestRunRaceEmptyHoldout(unittest.TestCase):
    def test_empty_holdout_returns_error(self):
        with patch("lab.competition.eval_harness._fetch_holdout", return_value=[]):
            result = run_race()
        self.assertIn("error", result)
        self.assertEqual(result["holdout_rows"], 0)


if __name__ == "__main__":
    unittest.main()

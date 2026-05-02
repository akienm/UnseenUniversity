"""
Tests for T-337: space word-graph passes with inter-trial intervals.

Covers:
- _next_pass_ts: correct timestamp offset per pass_count
- _next_pass_ts: clamped to last interval when pass_count exceeds list
- schedule_training_passes: sets next_pass_ts on complete books, skips non-complete
- schedule_training_passes: idempotent on second call (no reset)
- schedule_training_passes: reset=True rebuilds from train_ts
- train_due_passes: dry_run lists due books without training
- train_due_passes: skips books not yet due
- train_due_passes: increments pass_count and advances next_pass_ts after training
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))

from igor.cognition.training_corpus import (
    SPACING_INTERVALS_DAYS,
    _next_pass_ts,
    schedule_training_passes,
    train_due_passes,
)


class TestNextPassTs(unittest.TestCase):
    def test_pass_0_uses_first_interval(self):
        anchor = "2026-03-10T07:00:00"
        result = _next_pass_ts(0, anchor)
        expected_dt = datetime(2026, 3, 10, 7, 0, 0) + timedelta(
            days=SPACING_INTERVALS_DAYS[0]
        )
        self.assertEqual(result, expected_dt.strftime("%Y-%m-%dT%H:%M:%S"))

    def test_pass_1_uses_second_interval(self):
        anchor = "2026-03-10T07:00:00"
        result = _next_pass_ts(1, anchor)
        expected_dt = datetime(2026, 3, 10, 7, 0, 0) + timedelta(
            days=SPACING_INTERVALS_DAYS[1]
        )
        self.assertEqual(result, expected_dt.strftime("%Y-%m-%dT%H:%M:%S"))

    def test_pass_count_beyond_list_clamps_to_last(self):
        anchor = "2026-03-10T07:00:00"
        big_pass = len(SPACING_INTERVALS_DAYS) + 10
        result = _next_pass_ts(big_pass, anchor)
        expected_dt = datetime(2026, 3, 10, 7, 0, 0) + timedelta(
            days=SPACING_INTERVALS_DAYS[-1]
        )
        self.assertEqual(result, expected_dt.strftime("%Y-%m-%dT%H:%M:%S"))

    def test_pass_exactly_at_last_interval(self):
        anchor = "2026-03-10T07:00:00"
        last_idx = len(SPACING_INTERVALS_DAYS) - 1
        result = _next_pass_ts(last_idx, anchor)
        expected_dt = datetime(2026, 3, 10, 7, 0, 0) + timedelta(
            days=SPACING_INTERVALS_DAYS[-1]
        )
        self.assertEqual(result, expected_dt.strftime("%Y-%m-%dT%H:%M:%S"))


def _make_index(books: list[dict]) -> dict:
    return {b["id"]: b for b in books}


class TestScheduleTrainingPasses(unittest.TestCase):
    def _index_with(
        self,
        status="complete",
        train_ts="2026-03-10T07:00:00",
        next_pass_ts=None,
        pass_count=0,
    ):
        return {
            "title": "Test Book",
            "status": status,
            "train_ts": train_ts,
            "next_pass_ts": next_pass_ts,
            "pass_count": pass_count,
        }

    def test_schedules_complete_books(self):
        index = {"book1": self._index_with()}
        with patch(
            "igor.cognition.training_corpus._load_index", return_value=index
        ), patch("igor.cognition.training_corpus._save_index") as mock_save:
            result = schedule_training_passes()
        self.assertIn("1 scheduled", result)
        # next_pass_ts should now be set
        self.assertIsNotNone(index["book1"]["next_pass_ts"])
        mock_save.assert_called_once()

    def test_skips_pending_books(self):
        index = {"book1": self._index_with(status="pending")}
        with patch(
            "igor.cognition.training_corpus._load_index", return_value=index
        ), patch("igor.cognition.training_corpus._save_index") as mock_save:
            result = schedule_training_passes()
        self.assertIn("0 scheduled", result)
        mock_save.assert_not_called()

    def test_idempotent_second_call(self):
        index = {"book1": self._index_with(next_pass_ts="2026-03-11T07:00:00")}
        with patch(
            "igor.cognition.training_corpus._load_index", return_value=index
        ), patch("igor.cognition.training_corpus._save_index") as mock_save:
            result = schedule_training_passes()
        self.assertIn("0 scheduled", result)
        self.assertIn("1 already had a schedule", result)
        mock_save.assert_not_called()

    def test_reset_rebuilds_schedule(self):
        train_ts = "2026-03-10T07:00:00"
        old_next = "2030-01-01T00:00:00"
        index = {
            "book1": self._index_with(
                train_ts=train_ts, next_pass_ts=old_next, pass_count=3
            )
        }
        with patch(
            "igor.cognition.training_corpus._load_index", return_value=index
        ), patch("igor.cognition.training_corpus._save_index"):
            schedule_training_passes(reset=True)
        # pass_count reset to 0, next_pass_ts recalculated from train_ts
        self.assertEqual(index["book1"]["pass_count"], 0)
        expected = _next_pass_ts(0, train_ts)
        self.assertEqual(index["book1"]["next_pass_ts"], expected)

    def test_skips_complete_without_train_ts(self):
        index = {
            "book1": {
                "title": "No TS",
                "status": "complete",
                "train_ts": None,
                "next_pass_ts": None,
                "pass_count": 0,
            }
        }
        with patch(
            "igor.cognition.training_corpus._load_index", return_value=index
        ), patch("igor.cognition.training_corpus._save_index") as mock_save:
            result = schedule_training_passes()
        self.assertIn("0 scheduled", result)
        mock_save.assert_not_called()


class TestTrainDuePasses(unittest.TestCase):
    def _make_due_index(self, past_ts="2000-01-01T00:00:00"):
        return {
            "book1": {
                "title": "Due Book",
                "status": "complete",
                "train_ts": "2026-03-10T07:00:00",
                "next_pass_ts": past_ts,
                "pass_count": 0,
                "para_cursor": 100,
            }
        }

    def test_dry_run_lists_due_books(self):
        index = self._make_due_index()
        with patch(
            "igor.cognition.training_corpus._load_index", return_value=index
        ), patch("igor.cognition.training_corpus._save_index"):
            result = train_due_passes(dry_run=True)
        self.assertIn("Due for re-training", result)
        self.assertIn("Due Book", result)

    def test_nothing_due_returns_message(self):
        future_ts = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
        index = self._make_due_index(past_ts=future_ts)
        with patch("igor.cognition.training_corpus._load_index", return_value=index):
            result = train_due_passes(dry_run=False)
        self.assertIn("No training passes due", result)

    def test_missing_text_file_is_skipped(self):
        index = self._make_due_index()
        with patch(
            "igor.cognition.training_corpus._load_index", return_value=index
        ), patch("igor.cognition.training_corpus._save_index"), patch(
            "igor.cognition.training_corpus._disk_free_gb", return_value=5.0
        ), patch(
            "igor.cognition.word_graph.WordGraph", return_value=MagicMock()
        ), patch(
            "pathlib.Path.exists", return_value=False
        ):
            result = train_due_passes(dry_run=False)
        self.assertIn("missing", result)

    def test_pass_count_increments_after_training(self):
        index = self._make_due_index()
        mock_wg = MagicMock()

        def fake_train(book_id, wg, save_path=None):
            # Simulate train() completing — set status=complete
            index[book_id]["status"] = "complete"
            index[book_id]["train_ts"] = "2026-03-24T00:00:00"
            return f"Trained '{index[book_id]['title']}': 50 paragraphs indexed"

        with patch(
            "igor.cognition.training_corpus._load_index", return_value=index
        ), patch("igor.cognition.training_corpus._save_index"), patch(
            "igor.cognition.training_corpus._disk_free_gb", return_value=5.0
        ), patch(
            "igor.cognition.training_corpus.train", side_effect=fake_train
        ), patch(
            "igor.cognition.word_graph.WordGraph", return_value=mock_wg
        ), patch.object(
            Path, "exists", return_value=True
        ):
            result = train_due_passes(dry_run=False)

        self.assertEqual(index["book1"]["pass_count"], 1)
        # next_pass_ts should have advanced
        self.assertIsNotNone(index["book1"]["next_pass_ts"])
        self.assertIn("Pass #1", result)


if __name__ == "__main__":
    unittest.main()

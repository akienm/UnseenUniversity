"""
tests/test_predictive_self_modeling.py — T-predictive-self-modeling

Tests cover:
  - compare_prediction_to_result: match/mismatch/no-comparison cases
  - _tokenize: stopword removal, short-word filtering
  - push_deferred_result_to_twm: prediction notes use source=deferred_prediction
  - evaluate_deferred_predictions: match fires milieu reward + ring entry,
    mismatch fires ring entry only
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch


def _add_repo():
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo()


class TestTokenize(unittest.TestCase):
    def _tok(self, text):
        from devices.igor.tools.deferred_self_task import _tokenize

        return _tokenize(text)

    def test_removes_stopwords(self):
        tokens = self._tok("I expect to find the reading list")
        self.assertNotIn("the", tokens)
        self.assertNotIn("to", tokens)
        self.assertNotIn("i", tokens)

    def test_removes_short_words(self):
        tokens = self._tok("an in of a at")
        self.assertEqual(tokens, frozenset())

    def test_lowercases(self):
        tokens = self._tok("Reading List INBOX")
        self.assertIn("reading", tokens)
        self.assertIn("list", tokens)
        self.assertIn("inbox", tokens)

    def test_keeps_meaningful_words(self):
        tokens = self._tok("prediction memory search cortex query")
        self.assertIn("prediction", tokens)
        self.assertIn("memory", tokens)
        self.assertIn("cortex", tokens)


class TestComparePredictionToResult(unittest.TestCase):
    def _cmp(self, pred, result):
        from devices.igor.tools.deferred_self_task import compare_prediction_to_result

        return compare_prediction_to_result(pred, result)

    def test_identical_texts_match(self):
        score, label = self._cmp(
            "reading list inbox status", "reading list inbox status"
        )
        self.assertEqual(label, "MATCH")
        self.assertAlmostEqual(score, 1.0)

    def test_overlapping_keywords_match(self):
        score, label = self._cmp(
            "I expect to find reading list entries about Python",
            "memory_search('reading list'): 3 hit(s)\n  [FACTUAL] Python book in reading list",
        )
        self.assertEqual(label, "MATCH")
        self.assertGreater(score, 0.15)

    def test_unrelated_texts_mismatch(self):
        score, label = self._cmp(
            "I expect to find budget balance information",
            "memory_search('inbox'): 2 hits — email from Akien, calendar event",
        )
        self.assertEqual(label, "MISMATCH")
        self.assertLess(score, 0.15)

    def test_empty_prediction_no_comparison(self):
        score, label = self._cmp("", "some result text here")
        self.assertEqual(label, "NO_COMPARISON")
        self.assertEqual(score, 0.0)

    def test_empty_result_no_comparison(self):
        score, label = self._cmp("prediction text here", "")
        self.assertEqual(label, "NO_COMPARISON")

    def test_score_is_float(self):
        score, _ = self._cmp("reading list books", "reading books found")
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


class TestPushDeferredResultSource(unittest.TestCase):
    """Verify prediction notes use source=deferred_prediction."""

    def _push(self, title, result):
        from devices.igor.tools.deferred_self_task import push_deferred_result_to_twm

        mock_cortex = MagicMock()
        push_deferred_result_to_twm(mock_cortex, "job-123", title, result)
        return mock_cortex

    def test_prediction_note_uses_deferred_prediction_source(self):
        mc = self._push(
            "deferred_self_task:note:prediction: expect reading list",
            "self_note: prediction: expect reading list",
        )
        mc.twm_push.assert_called_once()
        kwargs = mc.twm_push.call_args[1]
        self.assertEqual(kwargs["source"], "deferred_prediction")

    def test_prediction_result_by_content_prefix(self):
        mc = self._push(
            "deferred_self_task:note:check this", "self_note: prediction: I expect X"
        )
        mc.twm_push.assert_called_once()
        kwargs = mc.twm_push.call_args[1]
        self.assertEqual(kwargs["source"], "deferred_prediction")

    def test_fetch_result_uses_deferred_self_task_source(self):
        mc = self._push(
            "deferred_self_task:memory_search:reading list",
            "memory_search('reading list'): 3 hits",
        )
        mc.twm_push.assert_called_once()
        kwargs = mc.twm_push.call_args[1]
        self.assertEqual(kwargs["source"], "deferred_self_task")

    def test_non_deferred_title_skips(self):
        mc = self._push("some_other_job:title", "result")
        mc.twm_push.assert_not_called()


class TestEvaluateDeferredPredictions(unittest.TestCase):

    def _make_twm_item(self, source, content):
        return {"source": source, "content_csb": content}

    def test_match_fires_milieu_reward(self):
        import devices.igor.cognition.milieu as milieu_mod
        from devices.igor.tools.deferred_self_task import (
            evaluate_deferred_predictions,
        )

        mock_cortex = MagicMock()
        mock_instance = MagicMock()
        mock_cortex.twm_read.return_value = [
            self._make_twm_item(
                "deferred_prediction",
                "DEFERRED_RESULT|job_id=abc|self_note: prediction: reading list books python",
            ),
            self._make_twm_item(
                "deferred_self_task",
                "DEFERRED_RESULT|job_id=xyz|memory_search('reading list'): Python books found",
            ),
        ]

        with patch.object(milieu_mod, "get", return_value=mock_instance):
            evaluate_deferred_predictions(mock_cortex)

        mock_instance.ingest_resolution_reward.assert_called_once()
        reward_val = mock_instance.ingest_resolution_reward.call_args[0][0]
        self.assertGreater(reward_val, 0.0)
        self.assertLessEqual(reward_val, 0.8)

    def test_mismatch_writes_ring_no_reward(self):
        import devices.igor.cognition.milieu as milieu_mod
        from devices.igor.tools.deferred_self_task import (
            evaluate_deferred_predictions,
        )

        mock_cortex = MagicMock()
        mock_instance = MagicMock()
        mock_cortex.twm_read.return_value = [
            self._make_twm_item(
                "deferred_prediction",
                "DEFERRED_RESULT|job_id=abc|self_note: prediction: budget balance remaining",
            ),
            self._make_twm_item(
                "deferred_self_task",
                "DEFERRED_RESULT|job_id=xyz|memory_search('inbox'): calendar events found",
            ),
        ]

        with patch.object(milieu_mod, "get", return_value=mock_instance):
            evaluate_deferred_predictions(mock_cortex)

        mock_cortex.write_ring.assert_called()
        ring_content = mock_cortex.write_ring.call_args[0][0]
        self.assertIn("PREDICTION_MISMATCH", ring_content)
        mock_instance.ingest_resolution_reward.assert_not_called()

    def test_no_predictions_is_noop(self):
        from devices.igor.tools.deferred_self_task import (
            evaluate_deferred_predictions,
        )

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = [
            self._make_twm_item(
                "deferred_self_task", "DEFERRED_RESULT|job_id=xyz|some result"
            ),
        ]
        evaluate_deferred_predictions(mock_cortex)
        mock_cortex.write_ring.assert_not_called()

    def test_no_results_is_noop(self):
        from devices.igor.tools.deferred_self_task import (
            evaluate_deferred_predictions,
        )

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = [
            self._make_twm_item(
                "deferred_prediction",
                "DEFERRED_RESULT|job_id=abc|self_note: prediction: something",
            ),
        ]
        evaluate_deferred_predictions(mock_cortex)
        mock_cortex.write_ring.assert_not_called()

    def test_match_writes_prediction_match_ring(self):
        from devices.igor.tools.deferred_self_task import (
            evaluate_deferred_predictions,
        )

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = [
            self._make_twm_item(
                "deferred_prediction",
                "DEFERRED_RESULT|job_id=a|self_note: prediction: reading list python books",
            ),
            self._make_twm_item(
                "deferred_self_task",
                "DEFERRED_RESULT|job_id=b|memory_search: python reading list 3 hits",
            ),
        ]

        with patch("devices.igor.cognition.milieu") as mock_milieu_mod:
            mock_milieu_mod.get.return_value = MagicMock()
            evaluate_deferred_predictions(mock_cortex)

        mock_cortex.write_ring.assert_called()
        content = mock_cortex.write_ring.call_args[0][0]
        self.assertIn("PREDICTION_MATCH", content)
        self.assertIn("score=", content)


if __name__ == "__main__":
    unittest.main()

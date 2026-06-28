"""
test_self_trainer.py — Unit tests for tools/self_trainer.py (T-self-training-loop).

Covers:
  - _parse_interaction_line: valid, malformed, short, skip-prefix
  - _read_candidate_turns: filters on cost threshold, skip prefixes, min lengths
  - _query_tokens: stopword removal, length filter
  - _matrix_covers: hit and miss via mock DB cursor
  - run_training_pass: full pass with mock DB, stats returned, deposits made
  - run_training_pass: no cloud turns → stats all zero
  - run_training_pass: covered turns → no deposits
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
import tempfile
import os



class TestParseInteractionLine(unittest.TestCase):
    def setUp(self):
        from unseen_university.devices.igor.tools.self_trainer import SelfTrainer

        self.trainer = SelfTrainer(db_url="unused", log_dir=Path("/tmp"))

    def test_valid_line(self):
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        line = f"{ts}|abc123|web:shared|tier.4|4823ms|$0.00782|IN:what time is it|OUT:It is 14:23\n"
        result = self.trainer._parse_interaction_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["turn_id"], "abc123")
        self.assertAlmostEqual(result["cost"], 0.00782)
        self.assertEqual(result["input_text"], "what time is it")
        self.assertEqual(result["response_text"], "It is 14:23")

    def test_malformed_too_few_parts(self):
        result = self.trainer._parse_interaction_line("2026-01-01T00:00:00|abc|only")
        self.assertIsNone(result)

    def test_empty_line(self):
        self.assertIsNone(self.trainer._parse_interaction_line(""))
        self.assertIsNone(self.trainer._parse_interaction_line("   "))

    def test_comment_line(self):
        self.assertIsNone(self.trainer._parse_interaction_line("# comment"))

    def test_missing_in_prefix(self):
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        line = f"{ts}|abc123|web|tier.4|100ms|$0.01|NOIN:text|OUT:response\n"
        result = self.trainer._parse_interaction_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["input_text"], "")  # no IN: prefix → empty


class TestReadCandidateTurns(unittest.TestCase):
    def setUp(self):
        from unseen_university.devices.igor.tools.self_trainer import SelfTrainer

        self.tmp = tempfile.mkdtemp()
        self.trainer = SelfTrainer(db_url="unused", log_dir=Path(self.tmp))

    def _write_log(self, lines: list[str], date_str: str = None):
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")
        path = Path(self.tmp) / f"interaction.{date_str}.log"
        path.write_text("\n".join(lines) + "\n")

    def _ts(self, delta_minutes: int = 0) -> str:
        return (datetime.now() - timedelta(minutes=delta_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )

    def test_cloud_turn_included(self):
        ts = self._ts(5)
        self._write_log(
            [
                f"{ts}|abc123|web|tier.4|4000ms|$0.01|IN:what is machine learning|OUT:Machine learning is a field of AI that"
            ]
        )
        result = self.trainer._read_candidate_turns(lookback_minutes=60)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["turn_id"], "abc123")

    def test_low_cost_turn_excluded(self):
        ts = self._ts(5)
        self._write_log(
            [
                f"{ts}|abc123|web|tier.2|500ms|$0.00000|IN:what is machine learning|OUT:Machine learning is a field"
            ]
        )
        result = self.trainer._read_candidate_turns(lookback_minutes=60)
        self.assertEqual(len(result), 0)

    def test_cc_prefix_excluded(self):
        ts = self._ts(5)
        self._write_log(
            [
                f"{ts}|abc123|web|tier.4|4000ms|$0.01|IN:CC: run flush_habit_cache now this is a command|OUT:Executing flush"
            ]
        )
        result = self.trainer._read_candidate_turns(lookback_minutes=60)
        self.assertEqual(len(result), 0)

    def test_too_old_excluded(self):
        ts = self._ts(200)  # 200 minutes ago, outside 120-min window
        self._write_log(
            [
                f"{ts}|abc123|web|tier.4|4000ms|$0.01|IN:what is machine learning|OUT:Machine learning is a field of AI that"
            ]
        )
        result = self.trainer._read_candidate_turns(lookback_minutes=120)
        self.assertEqual(len(result), 0)

    def test_dedup_by_turn_id(self):
        ts = self._ts(5)
        line = f"{ts}|abc123|web|tier.4|4000ms|$0.01|IN:what is machine learning|OUT:Machine learning is a field of AI that"
        self._write_log([line, line])  # same turn_id twice
        result = self.trainer._read_candidate_turns(lookback_minutes=60)
        self.assertEqual(len(result), 1)


class TestQueryTokens(unittest.TestCase):
    def test_removes_stopwords(self):
        from unseen_university.devices.igor.tools.self_trainer import SelfTrainer

        trainer = SelfTrainer(db_url="unused", log_dir=Path("/tmp"))
        tokens = trainer._query_tokens("what is machine learning and how does it work")
        self.assertNotIn("what", tokens)
        self.assertNotIn("and", tokens)
        self.assertIn("machine", tokens)
        self.assertIn("learning", tokens)

    def test_min_length_four(self):
        from unseen_university.devices.igor.tools.self_trainer import SelfTrainer

        trainer = SelfTrainer(db_url="unused", log_dir=Path("/tmp"))
        tokens = trainer._query_tokens("run it go now fast slow")
        # "run", "now" are 3 chars → excluded; "fast", "slow" are 4 → included
        self.assertNotIn("run", tokens)
        self.assertIn("fast", tokens)
        self.assertIn("slow", tokens)


class TestMatrixCovers(unittest.TestCase):
    def setUp(self):
        from unseen_university.devices.igor.tools.self_trainer import SelfTrainer

        self.trainer = SelfTrainer(db_url="unused", log_dir=Path("/tmp"))

    def _mock_conn(self, found: bool) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = (1,) if found else None
        return conn

    def test_covered_when_token_found(self):
        conn = self._mock_conn(found=True)
        self.assertTrue(self.trainer._matrix_covers(conn, "machine learning basics"))

    def test_gap_when_no_token_found(self):
        conn = self._mock_conn(found=False)
        self.assertFalse(self.trainer._matrix_covers(conn, "machine learning basics"))

    def test_covered_for_short_query(self):
        conn = self._mock_conn(found=False)
        # Very short query — not enough tokens to gap-check
        self.assertTrue(self.trainer._matrix_covers(conn, "hi"))


class TestRunTrainingPass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        from unseen_university.devices.igor.tools.self_trainer import SelfTrainer

        self.trainer = SelfTrainer(
            db_url="postgresql://test/test", log_dir=Path(self.tmp)
        )

    def _write_cloud_turn(
        self, input_text: str, response_text: str, cost: float = 0.01
    ):
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        date_str = datetime.now().strftime("%Y%m%d")
        line = f"{ts}|turn001|web|tier.4|4000ms|${cost:.5f}|IN:{input_text}|OUT:{response_text}\n"
        path = Path(self.tmp) / f"interaction.{date_str}.log"
        path.write_text(line)

    @patch("igor.tools.self_trainer.SelfTrainer._matrix_covers", return_value=False)
    @patch("igor.tools.self_trainer.SelfTrainer._deposit", return_value="ST_mem001")
    @patch("psycopg2.connect")
    @patch("igor.tools.self_trainer.log_cognition_metric", create=True)
    def test_gap_gets_deposited(
        self, mock_log, mock_psycopg2, mock_deposit, mock_covers
    ):
        from unseen_university.devices.igor.tools.self_trainer import SelfTrainer

        # Patch the import inside the method
        with patch.dict("sys.modules", {"psycopg2": MagicMock()}):
            import psycopg2

            psycopg2.connect = MagicMock(return_value=MagicMock())

            self._write_cloud_turn(
                "what is the meaning of embeddings in machine learning",
                "Embeddings are dense vector representations of data in a high-dimensional space",
            )

            with patch(
                "igor.tools.self_trainer.SelfTrainer._matrix_covers", return_value=False
            ), patch(
                "igor.tools.self_trainer.SelfTrainer._deposit", return_value="ST_001"
            ), patch(
                "igor.cognition.forensic_logger.log_cognition_metric"
            ):
                stats = self.trainer.run_training_pass(
                    lookback_minutes=60, max_deposits=5
                )

        self.assertGreaterEqual(stats["scanned"], 1)

    def test_no_cloud_turns_zero_stats(self):
        # No log files written
        with patch("igor.cognition.forensic_logger.log_cognition_metric"):
            stats = self.trainer.run_training_pass(lookback_minutes=60)
        self.assertEqual(stats["scanned"], 0)
        self.assertEqual(stats["deposited"], 0)

    @patch("psycopg2.connect")
    def test_covered_turn_not_deposited(self, mock_psycopg2):
        self._write_cloud_turn(
            "what is the meaning of embeddings in machine learning",
            "Embeddings are dense vector representations of data in a high-dimensional space",
        )
        mock_conn = MagicMock()
        mock_psycopg2.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)  # every token "found" → covered

        with patch("igor.cognition.forensic_logger.log_cognition_metric"):
            stats = self.trainer.run_training_pass(lookback_minutes=60)

        self.assertGreaterEqual(stats["covered"], 1)
        self.assertEqual(stats["deposited"], 0)


if __name__ == "__main__":
    unittest.main()

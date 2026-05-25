"""
test_output_trainer.py — Unit tests for tools/output_trainer.py.

Covers:
  - _parse_interaction_line: valid, malformed, empty
  - _read_candidate_turns: length filters, cost filter, skip patterns, dedup
  - _extract_trigger: stopwords, min length, token count
  - _trigger_already_covered: hit/miss/short
  - run_output_training_pass: seeds habit on gap, skips covered, zero stats on no turns
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))


class TestParseInteractionLine(unittest.TestCase):
    def setUp(self):
        from igor.tools.output_trainer import OutputTrainer

        self.trainer = OutputTrainer(db_url="unused", log_dir=Path("/tmp"))

    def test_valid_short_line(self):
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        line = f"{ts}|abc123|web|tier.4|1000ms|$0.01|IN:what is a basket|OUT:A basket is a shared dict\n"
        result = self.trainer._parse_interaction_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["input_text"], "what is a basket")
        self.assertEqual(result["response_text"], "A basket is a shared dict")
        self.assertAlmostEqual(result["cost"], 0.01)

    def test_malformed_returns_none(self):
        self.assertIsNone(self.trainer._parse_interaction_line("bad line"))
        self.assertIsNone(self.trainer._parse_interaction_line(""))

    def test_comment_line(self):
        self.assertIsNone(self.trainer._parse_interaction_line("# comment"))


class TestReadCandidateTurns(unittest.TestCase):
    def setUp(self):
        from igor.tools.output_trainer import OutputTrainer

        self.tmp = tempfile.mkdtemp()
        self.trainer = OutputTrainer(db_url="unused", log_dir=Path(self.tmp))

    def _write_log(self, lines, date_str=None):
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")
        path = Path(self.tmp) / f"interaction.{date_str}.log"
        path.write_text("\n".join(lines) + "\n")

    def _ts(self, delta_minutes=0):
        return (datetime.now() - timedelta(minutes=delta_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )

    def test_valid_short_turn_included(self):
        ts = self._ts(5)
        self._write_log(
            [
                f"{ts}|t01|web|tier.4|1000ms|$0.01|IN:what is a basket dict|OUT:A basket is shared dict passed through"
            ]
        )
        result = self.trainer._read_candidate_turns(60)
        self.assertEqual(len(result), 1)

    def test_too_long_input_excluded(self):
        ts = self._ts(5)
        long_input = "x" * 90
        self._write_log(
            [
                f"{ts}|t01|web|tier.4|1000ms|$0.01|IN:{long_input}|OUT:A short response here"
            ]
        )
        result = self.trainer._read_candidate_turns(60)
        self.assertEqual(len(result), 0)

    def test_too_long_response_excluded(self):
        ts = self._ts(5)
        long_resp = "x" * 210
        self._write_log(
            [f"{ts}|t01|web|tier.4|1000ms|$0.01|IN:what is this thing|OUT:{long_resp}"]
        )
        result = self.trainer._read_candidate_turns(60)
        self.assertEqual(len(result), 0)

    def test_time_query_skipped(self):
        ts = self._ts(5)
        self._write_log(
            [f"{ts}|t01|web|tier.4|1000ms|$0.01|IN:what time is it now|OUT:It is 14:23"]
        )
        result = self.trainer._read_candidate_turns(60)
        self.assertEqual(len(result), 0)

    def test_greeting_skipped(self):
        ts = self._ts(5)
        self._write_log(
            [
                f"{ts}|t01|web|tier.4|1000ms|$0.01|IN:hi there how are|OUT:I am well thanks"
            ]
        )
        result = self.trainer._read_candidate_turns(60)
        self.assertEqual(len(result), 0)

    def test_low_cost_excluded(self):
        ts = self._ts(5)
        self._write_log(
            [
                f"{ts}|t01|web|tier.2|1000ms|$0.00000|IN:what is a basket|OUT:A basket is shared dict"
            ]
        )
        result = self.trainer._read_candidate_turns(60)
        self.assertEqual(len(result), 0)

    def test_cc_prefix_excluded(self):
        ts = self._ts(5)
        self._write_log(
            [f"{ts}|t01|web|tier.4|1000ms|$0.01|IN:CC: run something|OUT:Done"]
        )
        result = self.trainer._read_candidate_turns(60)
        self.assertEqual(len(result), 0)

    def test_dedup_by_turn_id(self):
        ts = self._ts(5)
        line = f"{ts}|t01|web|tier.4|1000ms|$0.01|IN:what is a basket|OUT:A basket is shared dict passed through"
        self._write_log([line, line])
        result = self.trainer._read_candidate_turns(60)
        self.assertEqual(len(result), 1)


class TestExtractTrigger(unittest.TestCase):
    def setUp(self):
        from igor.tools.output_trainer import OutputTrainer

        self.trainer = OutputTrainer(db_url="unused", log_dir=Path("/tmp"))

    def test_removes_stopwords(self):
        trigger = self.trainer._extract_trigger("what is a basket dict")
        self.assertNotIn("what", trigger)
        self.assertIn("basket", trigger)
        self.assertIn("dict", trigger)

    def test_max_six_tokens(self):
        long_input = "alpha beta gamma delta epsilon zeta eta theta"
        tokens = self.trainer._extract_trigger(long_input).split()
        self.assertLessEqual(len(tokens), 6)

    def test_short_words_excluded(self):
        trigger = self.trainer._extract_trigger("run it go now fast slow")
        self.assertNotIn("run", trigger)
        self.assertIn("fast", trigger)

    def test_strips_web_message_prefix(self):
        """Web message prefix should not contaminate trigger keywords."""
        inp = (
            "TALKING WITH: Akien | relationship: operator\n"
            "[Web message from akien]: you are?"
        )
        trigger = self.trainer._extract_trigger(inp)
        self.assertNotIn("talking", trigger)
        self.assertNotIn("relationship", trigger)
        self.assertNotIn("operator", trigger)

    def test_strips_thread_context_prefix(self):
        """Full thread context + TALKING WITH prefix stripped before keyword extraction."""
        inp = (
            "[Thread context — recent exchanges in this channel:]\n"
            "  User: hello\n"
            "  Igor: Hello. Ready when you are.\n"
            "TALKING WITH: Akien | relationship: operator\n"
            "[Web message from akien]: how do you feel about threading?"
        )
        trigger = self.trainer._extract_trigger(inp)
        self.assertNotIn("talking", trigger)
        self.assertNotIn("akien", trigger)
        self.assertIn("threading", trigger)

    def test_strip_input_prefix_web_message(self):
        from igor.tools.output_trainer import OutputTrainer

        inp = "TALKING WITH: Akien | relationship: operator\n[Web message from akien]: you are?"
        result = OutputTrainer._strip_input_prefix(inp)
        self.assertEqual(result, "you are?")

    def test_strip_input_prefix_plain_text(self):
        from igor.tools.output_trainer import OutputTrainer

        inp = "what is a basket dict"
        result = OutputTrainer._strip_input_prefix(inp)
        self.assertEqual(result, "what is a basket dict")


class TestTriggerAlreadyCovered(unittest.TestCase):
    def setUp(self):
        from igor.tools.output_trainer import OutputTrainer

        self.trainer = OutputTrainer(db_url="unused", log_dir=Path("/tmp"))

    def _mock_conn(self, existing_triggers: list[str]) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        cur.fetchall.return_value = [(t,) for t in existing_triggers]
        return conn

    def test_covered_when_high_overlap(self):
        conn = self._mock_conn(["basket dict execution thread"])
        # trigger "basket dict execution" shares 3 tokens → covered
        self.assertTrue(
            self.trainer._trigger_already_covered(conn, "basket dict execution")
        )

    def test_not_covered_when_low_overlap(self):
        conn = self._mock_conn(["completely different topic here"])
        self.assertFalse(
            self.trainer._trigger_already_covered(conn, "basket dict execution thread")
        )

    def test_empty_existing_habits(self):
        conn = self._mock_conn([])
        self.assertFalse(
            self.trainer._trigger_already_covered(conn, "basket dict execution")
        )

    def test_short_trigger_covered_by_default(self):
        conn = self._mock_conn([])
        # Single token → too short to seed
        self.assertTrue(self.trainer._trigger_already_covered(conn, "basket"))


class TestRunOutputTrainingPass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        from igor.tools.output_trainer import OutputTrainer

        self.trainer = OutputTrainer(
            db_url="postgresql://test/test", log_dir=Path(self.tmp)
        )

    def _write_cloud_turn(self, input_text, response_text, cost=0.01):
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        date_str = datetime.now().strftime("%Y%m%d")
        line = f"{ts}|turn001|web|tier.4|1000ms|${cost:.5f}|IN:{input_text}|OUT:{response_text}\n"
        (Path(self.tmp) / f"interaction.{date_str}.log").write_text(line)

    def test_no_turns_zero_stats(self):
        with patch("igor.cognition.forensic_logger.log_cognition_metric"):
            stats = self.trainer.run_output_training_pass(lookback_minutes=60)
        self.assertEqual(stats["scanned"], 0)
        self.assertEqual(stats["seeded"], 0)

    @patch("psycopg2.connect")
    def test_covered_turn_not_seeded(self, mock_psycopg2):
        self._write_cloud_turn(
            "what is basket execution", "A basket is a shared dict per thread"
        )
        mock_conn = MagicMock()
        mock_psycopg2.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        # Return existing trigger with high overlap
        mock_cur.fetchall.return_value = [("basket execution thread shared",)]

        with patch("igor.cognition.forensic_logger.log_cognition_metric"):
            stats = self.trainer.run_output_training_pass(lookback_minutes=60)

        self.assertGreaterEqual(stats["skipped_covered"], 1)
        self.assertEqual(stats["seeded"], 0)

    @patch("psycopg2.connect")
    def test_uncovered_turn_seeded(self, mock_psycopg2):
        self._write_cloud_turn(
            "what is basket execution context", "A basket is a shared dict per thread"
        )
        mock_conn = MagicMock()
        mock_psycopg2.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        # No existing habits → not covered
        mock_cur.fetchall.return_value = []

        with patch("igor.cognition.forensic_logger.log_cognition_metric"):
            stats = self.trainer.run_output_training_pass(lookback_minutes=60)

        self.assertEqual(stats["seeded"], 1)
        # Verify INSERT was called
        mock_cur.execute.assert_called()


if __name__ == "__main__":
    unittest.main()

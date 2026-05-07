"""
test_daily_console_file_handler.py — DailyConsoleFileHandler unit tests.

Verifies: daily rollover, ANSI color codes in output, 7-day pruning,
and that setup_logging() wires the handler onto igor root.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wild_igor"))

from igor.logging_setup import DailyConsoleFileHandler, setup_logging


def _fresh_igor_root():
    igor = logging.getLogger("igor")
    for h in list(igor.handlers):
        igor.removeHandler(h)
    return igor


class TestDailyConsoleFileHandler(unittest.TestCase):
    def setUp(self):
        _fresh_igor_root()

    def test_creates_dated_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            h = DailyConsoleFileHandler(log_dir)
            record = logging.LogRecord(
                "igor.test", logging.INFO, "", 0, "hello", (), None
            )
            h.handle(record)
            h.close()
            files = list(log_dir.glob("????????.console.log"))
            self.assertEqual(len(files), 1)
            self.assertRegex(files[0].name, r"^\d{8}\.console\.log$")

    def test_output_contains_ansi_color_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            h = DailyConsoleFileHandler(log_dir)
            record = logging.LogRecord(
                "igor.test", logging.WARNING, "", 0, "warn msg", (), None
            )
            h.handle(record)
            h.close()
            content = next(log_dir.glob("*.console.log")).read_text(encoding="utf-8")
            self.assertIn("\x1b[", content, "Expected ANSI escape codes in console.log")

    def test_rolls_over_at_day_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            h = DailyConsoleFileHandler(log_dir)

            day1 = datetime(2026, 5, 6, 23, 59)
            day2 = datetime(2026, 5, 7, 0, 1)

            with patch("igor.logging_setup.datetime") as mock_dt:
                mock_dt.now.return_value = day1
                mock_dt.strptime = datetime.strptime
                r1 = logging.LogRecord("igor", logging.INFO, "", 0, "day1", (), None)
                h.handle(r1)

                mock_dt.now.return_value = day2
                r2 = logging.LogRecord("igor", logging.INFO, "", 0, "day2", (), None)
                h.handle(r2)

            h.close()
            files = sorted(log_dir.glob("????????.console.log"))
            self.assertEqual(len(files), 2)
            self.assertEqual(files[0].name, "20260506.console.log")
            self.assertEqual(files[1].name, "20260507.console.log")

    def test_prunes_files_older_than_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            # Create stale files
            old = log_dir / "20260101.console.log"
            recent = log_dir / "20260506.console.log"
            old.write_text("old", encoding="utf-8")
            recent.write_text("recent", encoding="utf-8")

            h = DailyConsoleFileHandler(log_dir, retention_days=7)
            record = logging.LogRecord("igor", logging.INFO, "", 0, "x", (), None)
            h.handle(record)
            h.close()

            self.assertFalse(old.exists(), "Stale file should have been pruned")
            self.assertTrue(recent.exists(), "Recent file should be kept")

    def test_setup_logging_wires_daily_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup_logging(Path(tmp))
            igor = logging.getLogger("igor")
            handler_types = [type(h).__name__ for h in igor.handlers]
            self.assertIn(
                "DailyConsoleFileHandler",
                handler_types,
                f"Expected DailyConsoleFileHandler on igor root, got {handler_types}",
            )


if __name__ == "__main__":
    unittest.main()

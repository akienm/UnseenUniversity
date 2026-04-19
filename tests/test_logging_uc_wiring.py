"""
test_logging_uc_wiring.py — T-slow-queries-yellow-regression

Verifies that setup_logging() attaches the ConsoleHandler to the
lab.utility_closet logger hierarchy, so WARNING-level messages from
the moved-out-of-igor infrastructure (db_proxy, comms, etc.) still
render in yellow on the terminal.

Without this wire, warnings from lab.utility_closet.* log to the
Python root logger with no formatting — the regression Akien spotted
on 2026-04-18.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wild_igor"))

from igor.logging_setup import ConsoleHandler, setup_logging


class TestUCLoggerWiring(unittest.TestCase):
    def setUp(self):
        # Clear existing handlers on BOTH roots so setup_logging is idempotent-fresh
        for name in ("igor", "lab.utility_closet"):
            logger = logging.getLogger(name)
            for h in list(logger.handlers):
                logger.removeHandler(h)

    def test_uc_logger_has_console_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup_logging(Path(tmp))
            uc = logging.getLogger("lab.utility_closet")
            handler_types = [type(h).__name__ for h in uc.handlers]
            self.assertIn(
                "ConsoleHandler",
                handler_types,
                f"Expected ConsoleHandler on lab.utility_closet, got {handler_types}",
            )

    def test_uc_warning_reaches_handler(self):
        """A warning logged to lab.utility_closet.db_proxy should reach the
        ConsoleHandler (emit is called). We verify by subclassing the console
        handler to count emits."""
        call_count = [0]

        class _Counter(ConsoleHandler):
            def emit(self, record):
                call_count[0] += 1

        with tempfile.TemporaryDirectory() as tmp:
            setup_logging(Path(tmp))
            # Replace the ConsoleHandler on uc logger with our counter variant
            uc = logging.getLogger("lab.utility_closet")
            for h in list(uc.handlers):
                if type(h).__name__ == "ConsoleHandler":
                    uc.removeHandler(h)
            counter = _Counter(level=logging.INFO)
            uc.addHandler(counter)

            child = logging.getLogger("lab.utility_closet.db_proxy")
            child.warning("[pg_proxy] slow query 75ms — SELECT 1")

        self.assertEqual(
            call_count[0],
            1,
            f"Expected 1 WARNING to reach the console handler, got {call_count[0]}",
        )

    def test_uc_logger_doesnt_propagate_to_root(self):
        """propagate=False ensures we don't double-emit via the Python root."""
        with tempfile.TemporaryDirectory() as tmp:
            setup_logging(Path(tmp))
            uc = logging.getLogger("lab.utility_closet")
            self.assertFalse(uc.propagate)


if __name__ == "__main__":
    unittest.main()

"""T-engram-logging-primitive: engram_log callable from code_ref execution.

Verifies:
  - engram_log appends structured entries to TurnContext["engram_logs"]
  - engram_log emits to Python logger at correct level
  - engram_execution_context injects habit_id into thread-local
  - SchedulerSource._call_tool wraps dispatch with engram_execution_context
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestEngramLogPrimitive:
    def test_engram_log_appends_to_turn_ctx(self):
        """engram_log appends a structured entry to TurnContext engram_logs list."""
        from wild_igor.igor.tools.engram_log import (
            engram_execution_context,
            engram_log,
        )
        from wild_igor.igor.cognition import forensic_logger

        forensic_logger.init_turn_ctx("t-001", "thread-x", "test input")
        try:
            with engram_execution_context(habit_id="hab-001", habit_name="my_tool"):
                engram_log("hello from habit", level="info")
                engram_log("second message", level="warning")

            ctx = forensic_logger._current_turn.ctx
            assert "engram_logs" in ctx, "engram_logs key missing from TurnContext"
            entries = ctx["engram_logs"]
            assert len(entries) == 2

            e0 = entries[0]
            assert e0["habit_id"] == "hab-001"
            assert e0["habit_name"] == "my_tool"
            assert e0["level"] == "info"
            assert e0["message"] == "hello from habit"
            assert "ts" in e0

            e1 = entries[1]
            assert e1["level"] == "warning"
            assert e1["message"] == "second message"
        finally:
            forensic_logger._current_turn.ctx = None

    def test_engram_log_without_turn_ctx_does_not_raise(self):
        """engram_log is safe when no TurnContext is active."""
        from wild_igor.igor.tools.engram_log import (
            engram_execution_context,
            engram_log,
        )
        from wild_igor.igor.cognition import forensic_logger

        forensic_logger._current_turn.ctx = None
        with engram_execution_context(habit_id="hab-002"):
            engram_log("no ctx", level="info")  # must not raise

    def test_context_manager_clears_on_exit(self):
        """Thread-local habit_id is None after the context manager exits."""
        from wild_igor.igor.tools.engram_log import (
            _ctx,
            engram_execution_context,
        )

        with engram_execution_context(habit_id="hab-003", habit_name="fn"):
            assert _ctx.habit_id == "hab-003"
        assert _ctx.habit_id is None
        assert _ctx.habit_name is None

    def test_scheduler_source_call_tool_wraps_context(self):
        """SchedulerSource._call_tool injects habit_id before calling the tool fn."""
        from wild_igor.igor.tools.engram_log import _ctx

        captured_habit_ids = []

        def _capturing_tool():
            captured_habit_ids.append(getattr(_ctx, "habit_id", None))
            return "ok"

        fake_tool = MagicMock()
        fake_tool.fn = _capturing_tool

        fake_registry = MagicMock()
        fake_registry.get.return_value = fake_tool

        # Import after patching to avoid circular issues
        from wild_igor.igor.cognition.push_sources import SchedulerSource

        src = SchedulerSource.__new__(SchedulerSource)

        with (
            patch(
                "wild_igor.igor.cognition.push_sources.SchedulerSource._call_tool",
                SchedulerSource._call_tool.__get__(src),
            ),
            patch(
                "wild_igor.igor.cognition.push_sources.registry",
                fake_registry,
                create=True,
            ),
        ):
            # Patch registry inside the method's import scope
            import lab.utility_closet.registry as reg_mod

            orig_get = reg_mod.registry.get
            reg_mod.registry.get = lambda name: fake_tool

            try:
                result = src._call_tool("mymod:my_habit", habit_id="hab-scheduler")
            finally:
                reg_mod.registry.get = orig_get

        assert captured_habit_ids == [
            "hab-scheduler"
        ], f"Expected habit_id injected, got {captured_habit_ids}"
        assert result == "ok"

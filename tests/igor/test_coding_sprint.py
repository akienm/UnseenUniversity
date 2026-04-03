"""
test_coding_sprint.py — Tests for T-programming-engrams first-cut implementation.

Covers:
  - goal_continuation step 3 includes the TWM push call (source inspection)
  - run_coding_sprint() skip paths (no ACTIVE_GOAL, no active GOAL memory)
  - PROC_CODING_SPRINT habit schema correctness (id, code_ref, twm_trigger)
"""

import importlib
import inspect
import re

import pytest
from unittest.mock import MagicMock, patch

# ── goal_continuation step 3 source inspection ────────────────────────────────


class TestGoalContinuationStep3TwmPush:
    """Verify that step 3 in goal_continuation includes the twm_push call."""

    def test_step3_calls_twm_push(self):
        """
        The step 3 handler must call cortex.twm_push() with GOAL_READY signal.
        Inspects the source code rather than running live (avoids DB dependency).
        """
        import wild_igor.igor.tools.goal_continuation as gc_mod

        src = inspect.getsource(gc_mod.run_goal_continuation)
        assert "twm_push" in src, "step 3 must call cortex.twm_push()"

    def test_step3_twm_push_uses_goal_ready(self):
        """The twm_push in step 3 must push a GOAL_READY content_csb."""
        import wild_igor.igor.tools.goal_continuation as gc_mod

        src = inspect.getsource(gc_mod.run_goal_continuation)
        assert "GOAL_READY" in src, "twm_push content_csb must include GOAL_READY"

    def test_step3_twm_push_category_goal_ready(self):
        """The twm_push in step 3 must use category='goal_ready'."""
        import wild_igor.igor.tools.goal_continuation as gc_mod

        src = inspect.getsource(gc_mod.run_goal_continuation)
        assert "goal_ready" in src, "twm_push must use category='goal_ready'"

    def test_step3_twm_push_has_ttl(self):
        """The twm_push in step 3 must set a TTL (sprint must fire in window)."""
        import wild_igor.igor.tools.goal_continuation as gc_mod

        src = inspect.getsource(gc_mod.run_goal_continuation)
        assert "ttl_seconds" in src, "twm_push in step 3 must set ttl_seconds"


# ── run_coding_sprint skip paths ───────────────────────────────────────────────


class TestRunCodingSprintSkipPaths:
    """run_coding_sprint() must return skip messages when preconditions unmet."""

    def test_no_active_goal_in_twm_falls_back_to_goal_memory(self):
        """
        If twm_get_active_goal() returns None but there's an active GOAL memory,
        run_coding_sprint falls back to source_message from the GOAL memory.
        If there's no active GOAL memory either, returns the GOAL memory skip msg.
        """
        from wild_igor.igor.tools.ops import run_coding_sprint

        mock_cortex = MagicMock()
        mock_cortex.twm_get_active_goal.return_value = None
        mock_cortex.get_by_type.return_value = []  # no active GOAL memories

        with patch("wild_igor.igor.memory.cortex.Cortex", return_value=mock_cortex):
            result = run_coding_sprint()

        assert "no active GOAL memory" in result
        assert "skipping" in result

    def test_no_active_goal_memory_returns_skip(self):
        """
        If twm has an active goal but no GOAL-type memory is active,
        run_coding_sprint returns skip msg.
        """
        from wild_igor.igor.tools.ops import run_coding_sprint

        mock_cortex = MagicMock()
        mock_cortex.twm_get_active_goal.return_value = "implement T-foo"
        # get_by_type returns goals, none with goal_active=True
        mock_cortex.get_by_type.return_value = [
            MagicMock(metadata={"goal_active": False}, narrative="old goal")
        ]

        with patch("wild_igor.igor.memory.cortex.Cortex", return_value=mock_cortex):
            result = run_coding_sprint()

        assert "no active GOAL memory" in result
        assert "skipping" in result

    def test_with_active_goal_runs_chain_and_evicts(self):
        """
        Happy path: active goal in TWM + active GOAL memory → calls run_pe_chain
        and evicts GOAL_READY from TWM.
        """
        from wild_igor.igor.tools.ops import run_coding_sprint

        mock_cortex = MagicMock()
        mock_cortex.twm_get_active_goal.return_value = "implement T-programming-engrams"

        mock_goal = MagicMock()
        mock_goal.metadata = {
            "goal_active": True,
            "adopted_at": "2026-04-01T10:00:00",
            "source_message": "T-programming-engrams: seed coding sprint",
        }
        mock_goal.narrative = "ACTIVE GOAL: implement T-programming-engrams"
        mock_cortex.get_by_type.return_value = [mock_goal]

        mock_chain = MagicMock(
            return_value="[pe_chain] DONE: ticket=T-programming-engrams"
        )

        # run_coding_sprint does `from .pe_chain import run_pe_chain` inside the
        # function body — patch the source so the local binding picks up the mock.
        with patch("wild_igor.igor.memory.cortex.Cortex", return_value=mock_cortex):
            with patch("wild_igor.igor.tools.pe_chain.run_pe_chain", mock_chain):
                result = run_coding_sprint()

        assert "coding_sprint" in result
        assert "T-programming-engrams" in result
        # Must evict GOAL_READY before running chain
        mock_cortex.twm_evict_category.assert_called_once_with("goal_ready")
        # Must call pe_chain
        mock_chain.assert_called_once()


# ── PROC_CODING_SPRINT habit schema ───────────────────────────────────────────


class TestProcCodingSprintHabitSchema:
    """PROC_CODING_SPRINT habit definition must be well-formed.

    The schema is defined inline here rather than importing the seed script,
    to avoid the seed script's module-level sys.path/environ mutations
    polluting the test process and breaking test_graph_integrator node IDs.
    The values here must stay in sync with seed_coding_sprint_habit.py.
    """

    @pytest.fixture
    def habit(self):
        """Return the PROC_CODING_SPRINT habit dict as defined in the seed script."""
        return {
            "id": "PROC_CODING_SPRINT",
            "narrative": (
                "When my TWM contains a GOAL_READY signal, I fire a coding sprint. "
                "I call run_coding_sprint(), which reads my current ACTIVE_GOAL and active "
                "GOAL memory details, then posts a structured coding prompt to the channel "
                "so the LLM can take over and implement the goal. After posting, the "
                "GOAL_READY signal is evicted from TWM — it has been consumed. "
                "This is the D300 reactive cascade: goal_continuation step 3 writes "
                "GOAL_READY → I fire → prompt posted → LLM sprints."
            ),
            "memory_type": "PROCEDURAL",
            "source": "seed",
            "confidence": 1.0,
            "context_of_encoding": "T-programming-engrams — seed_coding_sprint_habit 2026-04-01",
            "metadata": {
                "habit_type": "cognitive",
                "code_ref": "ops:run_coding_sprint",
                "twm_trigger": "GOAL_READY",
                "match_mode": "trigger_only",
                "proc_name": "PROC_CODING_SPRINT",
                "inertia": 0.3,
                "why": (
                    "T-programming-engrams D300: wires GOAL_READY TWM signal to coding "
                    "sprint execution. First step of the programming cascade: "
                    "GOAL_ADOPTED → (goal_continuation 0-3) → GOAL_READY in TWM → "
                    "PROC_CODING_SPRINT fires → implementation prompt posted → LLM sprints."
                ),
            },
        }

    def test_habit_id(self, habit):
        """PROC_CODING_SPRINT must have the correct id."""
        assert habit["id"] == "PROC_CODING_SPRINT"

    def test_habit_code_ref(self, habit):
        """habit metadata must have code_ref pointing to ops:run_coding_sprint."""
        assert habit["metadata"]["code_ref"] == "ops:run_coding_sprint"

    def test_habit_twm_trigger(self, habit):
        """habit metadata must declare twm_trigger=GOAL_READY."""
        assert habit["metadata"]["twm_trigger"] == "GOAL_READY"

    def test_habit_type_cognitive(self, habit):
        """habit_type must be cognitive (D300 reactive cascade)."""
        assert habit["metadata"]["habit_type"] == "cognitive"

    def test_habit_memory_type(self, habit):
        """memory_type must be PROCEDURAL."""
        assert habit["memory_type"] == "PROCEDURAL"

    def test_habit_inertia_present(self, habit):
        """inertia must be set (calibration signal)."""
        assert "inertia" in habit["metadata"]
        assert 0.0 < habit["metadata"]["inertia"] < 1.0

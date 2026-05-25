"""
tests/test_goal_close.py — Unit tests for close_goal_by_ticket (T-goal-close-habit).

Tests:
  - Finds active goal by ticket_id in source_message and marks it inactive
  - Returns "no active goal found" when no match
  - Case-insensitive ticket_id match

No Postgres, no filesystem I/O — Cortex is mocked.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _add_repo_to_path():
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo_to_path()

# Patch target: close_goal_by_ticket imports Cortex inside the function
_CORTEX_PATH = "devices.igor.memory.cortex.Cortex"
_MT_PATH = "devices.igor.memory.models.MemoryType"


def _make_goal(ticket_id: str, active: bool = True) -> MagicMock:
    g = MagicMock()
    g.id = f"GOAL_20260401120000000001"
    g.narrative = f"ACTIVE GOAL: work ticket {ticket_id}"
    g.metadata = {
        "goal_active": active,
        "source_message": f"work ticket {ticket_id}",
        "adopted_at": "2026-04-01T12:00:00Z",
        "failure_count": 0,
    }
    return g


def _run_close(ticket_id: str, goals: list) -> tuple[str, MagicMock]:
    """Call close_goal_by_ticket with mocked Cortex returning `goals`."""
    from devices.igor.tools.ops import close_goal_by_ticket

    mock_cortex = MagicMock()
    mock_cortex.get_by_type.return_value = goals

    mock_mt = MagicMock()
    mock_mt.GOAL = "GOAL"

    with patch(_CORTEX_PATH, return_value=mock_cortex), patch(_MT_PATH, mock_mt):
        result = close_goal_by_ticket(ticket_id)

    return result, mock_cortex


class TestCloseGoalByTicket(unittest.TestCase):

    def test_closes_matching_active_goal(self):
        """Finds active goal by ticket_id and marks goal_active=False."""
        goal = _make_goal("T-phase-d-ex4")
        result, cortex = _run_close("T-phase-d-ex4", [goal])

        self.assertIn("closed", result)
        self.assertIn("T-phase-d-ex4", result)
        self.assertFalse(goal.metadata["goal_active"])
        self.assertIn("closed_at", goal.metadata)
        cortex.store.assert_called_once_with(goal)

    def test_returns_not_found_when_no_match(self):
        """Returns 'no active goal found' when no active goal matches ticket_id."""
        goal = _make_goal("T-other-ticket")
        result, cortex = _run_close("T-phase-d-ex4", [goal])

        self.assertIn("no active goal found", result)
        self.assertIn("T-phase-d-ex4", result)
        # goal should be untouched
        self.assertTrue(goal.metadata["goal_active"])
        cortex.store.assert_not_called()

    def test_returns_not_found_when_no_active_goals(self):
        """Returns 'no active goal found' when there are no active goals at all."""
        inactive_goal = _make_goal("T-phase-d-ex4", active=False)
        result, cortex = _run_close("T-phase-d-ex4", [inactive_goal])

        self.assertIn("no active goal found", result)
        cortex.store.assert_not_called()

    def test_case_insensitive_match(self):
        """ticket_id match is case-insensitive."""
        # source_message has uppercase; ticket_id passed lowercase
        goal = _make_goal("T-PHASE-D-EX4")
        result, _ = _run_close("t-phase-d-ex4", [goal])

        self.assertIn("closed", result)
        self.assertFalse(goal.metadata["goal_active"])

    def test_case_insensitive_match_reversed(self):
        """ticket_id match is case-insensitive — uppercase arg, lowercase stored."""
        goal = _make_goal("t-phase-d-ex4")
        result, _ = _run_close("T-PHASE-D-EX4", [goal])

        self.assertIn("closed", result)
        self.assertFalse(goal.metadata["goal_active"])

    def test_narrative_updated_on_close(self):
        """Narrative gets 'Status: CLOSED' appended when goal is closed."""
        goal = _make_goal("T-phase-d-ex4")
        _run_close("T-phase-d-ex4", [goal])

        self.assertIn("CLOSED", goal.narrative)

    def test_returns_goal_id_in_confirmation(self):
        """Confirmation message includes the goal_id."""
        goal = _make_goal("T-phase-d-ex4")
        result, _ = _run_close("T-phase-d-ex4", [goal])

        self.assertIn(goal.id, result)

    def test_empty_goals_list(self):
        """Works cleanly with empty goals list."""
        result, cortex = _run_close("T-anything", [])

        self.assertIn("no active goal found", result)
        cortex.store.assert_not_called()


if __name__ == "__main__":
    unittest.main()

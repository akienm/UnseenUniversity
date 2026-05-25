"""
test_thread_context_persistence.py — Tests for T-thread-context-persistence.

Covers:
  - store_plan: verifies INSERT ON CONFLICT SQL called with right params
  - read_active_goal_plan: no active goals → returns "no active GOAL memory"
  - read_active_goal_plan: active goal but no plan in traversal_contexts → "no plan stored"
  - read_active_goal_plan: active goal and plan stored → formatted string with ticket_id + plan
"""

from unittest.mock import MagicMock, patch, call

# ── store_plan ─────────────────────────────────────────────────────────────────


class TestStorePlan:
    """store_plan() upserts to traversal_contexts with correct SQL and params."""

    def test_store_plan_executes_upsert(self):
        """store_plan must call execute with INSERT ON CONFLICT SQL and correct params."""
        from devices.igor.tools.ops import store_plan

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg2.connect", return_value=mock_conn):
            result = store_plan("T-my-ticket", "Read X, add Y, test Z.")

        # Must have called execute once
        mock_cursor.execute.assert_called_once()
        sql, params = mock_cursor.execute.call_args[0]

        # SQL must be an upsert
        assert "ON CONFLICT" in sql
        assert "DO UPDATE SET" in sql
        assert "traversal_contexts" in sql

        # Params: context_id, key, value, step=0 is embedded in SQL, recorded_at
        assert params[0] == "T-my-ticket"
        assert params[1] == "plan"
        assert params[2] == "Read X, add Y, test Z."

        # Result must confirm storage
        assert "T-my-ticket" in result

    def test_store_plan_returns_confirmation(self):
        """store_plan must return a confirmation string containing the ticket_id."""
        from devices.igor.tools.ops import store_plan

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg2.connect", return_value=mock_conn):
            result = store_plan("T-foo-bar", "Plan text here.")

        assert "T-foo-bar" in result
        assert "[store_plan]" in result

    def test_store_plan_db_error_returns_error_string(self):
        """store_plan must catch DB errors and return an error string, not raise."""
        from devices.igor.tools.ops import store_plan

        with patch("psycopg2.connect", side_effect=Exception("connection refused")):
            result = store_plan("T-any", "any plan")

        assert "[ERROR]" in result
        assert "store_plan" in result


# ── read_active_goal_plan ──────────────────────────────────────────────────────


class TestReadActiveGoalPlan:
    """read_active_goal_plan() returns correct strings for all states."""

    def test_no_active_goals_returns_no_active_goal_memory(self):
        """When no active GOAL memories exist, return 'no active GOAL memory'."""
        from devices.igor.tools.ops import read_active_goal_plan

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = []  # no goals at all

        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            result = read_active_goal_plan()

        assert "no active GOAL memory" in result

    def test_active_goal_no_plan_returns_no_plan_stored(self):
        """When active goal exists but traversal_contexts has no row, return 'no plan stored'."""
        from devices.igor.tools.ops import read_active_goal_plan

        mock_goal = MagicMock()
        mock_goal.metadata = {
            "goal_active": True,
            "adopted_at": "2026-04-02T10:00:00",
            "source_message": "work ticket T-my-ticket",
        }
        mock_goal.narrative = "ACTIVE GOAL: work T-my-ticket"

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [mock_goal]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # no row in traversal_contexts
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            with patch("psycopg2.connect", return_value=mock_conn):
                result = read_active_goal_plan()

        assert "no plan stored" in result
        assert "T-my-ticket" in result

    def test_active_goal_with_plan_returns_formatted_string(self):
        """When active goal has a stored plan, return formatted '[active_goal_plan] T-xxx: ...'."""
        from devices.igor.tools.ops import read_active_goal_plan

        mock_goal = MagicMock()
        mock_goal.metadata = {
            "goal_active": True,
            "adopted_at": "2026-04-02T10:00:00",
            "source_message": "implement T-thread-context-persistence",
        }
        mock_goal.narrative = "ACTIVE GOAL: implement T-thread-context-persistence"

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [mock_goal]

        plan_text = "Read ops.py, add store_plan + read_active_goal_plan, seed habit, run tests."
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (plan_text,)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor

        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            with patch("psycopg2.connect", return_value=mock_conn):
                result = read_active_goal_plan()

        assert "[active_goal_plan]" in result
        assert "T-thread-context-persistence" in result
        assert (
            "store_plan" in result
        )  # plan text truncated to 400 chars — still present

    def test_active_goal_no_ticket_id_in_source(self):
        """When active goal source_message has no T-xxx pattern, return informative message."""
        from devices.igor.tools.ops import read_active_goal_plan

        mock_goal = MagicMock()
        mock_goal.metadata = {
            "goal_active": True,
            "adopted_at": "2026-04-02T10:00:00",
            "source_message": "do something vague with no ticket reference",
        }
        mock_goal.narrative = "ACTIVE GOAL: do something vague"

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [mock_goal]

        with patch("devices.igor.memory.cortex.Cortex", return_value=mock_cortex):
            result = read_active_goal_plan()

        assert "no ticket ID found" in result

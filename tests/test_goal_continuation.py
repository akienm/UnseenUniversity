"""
tests/test_goal_continuation.py — Unit tests for D274 goal_continuation.py.

Tests cover the step-machine logic: step 0 (claim), step 1 (show+grep_for parse),
step 2 (grep or skip), step 3 (ready signal), step 4+ (LLM territory).

No Postgres, no filesystem I/O — all external calls are mocked.

Ref: T-phase-d-canonical / Phase D ex4
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


def _make_goal(
    step: int, grep_for: list | None = None, task: str = "work ticket T-test-001"
) -> MagicMock:
    g = MagicMock()
    g.id = "goal-001"
    g.narrative = task
    g.metadata = {
        "goal_active": True,
        "adopted_at": "2026-04-01T00:00:00Z",
        "source_message": task,
        "current_step": step,
    }
    if grep_for is not None:
        g.metadata["grep_for"] = grep_for
    return g


# Patch paths: Cortex/MemoryType are imported inside run_goal_continuation()
# via `from ..memory.cortex import Cortex as _Cortex` — patch the source.
_CORTEX_PATH = "wild_igor.igor.memory.cortex.Cortex"
_MT_PATH = "wild_igor.igor.memory.models.MemoryType"


def _run_step(goal, bash_returns=None, ticket_data=None):
    """
    Call run_goal_continuation with one goal and mock I/O.
    - bash_returns: list of strings returned sequentially by _run_bash.
    - ticket_data: dict returned by _load_ticket (grep_for parsing in step 1).
    Returns (result_str, posted_messages).
    """
    from wild_igor.igor.tools import goal_continuation as gc
    from wild_igor.igor.tools.goal_continuation import GoalContinuation

    bash_iter = iter(bash_returns or [])
    posted = []

    mock_cortex = MagicMock()
    mock_cortex.get_by_type.return_value = [goal]

    mock_mt = MagicMock()
    mock_mt.GOAL = "GOAL"

    with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
        _MT_PATH, mock_mt
    ), patch.object(
        GoalContinuation,
        "_run_bash",
        side_effect=lambda _: next(bash_iter, "(no output)"),
    ), patch.object(
        GoalContinuation, "_load_ticket", return_value=ticket_data
    ), patch.object(
        gc, "_post_to_channel", side_effect=posted.append
    ):
        result = gc.run_goal_continuation()

    return result, posted, mock_cortex


class TestGoalContinuationSteps(unittest.TestCase):
    """Step-machine logic — all external I/O mocked."""

    def test_no_active_goals(self):
        from wild_igor.igor.tools import goal_continuation as gc

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = []
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"
        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(_MT_PATH, mock_mt):
            result = gc.run_goal_continuation()
        self.assertIn("no active goals", result)

    def test_step0_advances(self):
        """Step 0 advances to step 1 (claiming removed — ticket already in_progress)."""
        goal = _make_goal(step=0)
        result, posted, cortex = _run_step(goal)
        self.assertIn("advancing", result)
        self.assertEqual(goal.metadata["current_step"], 1)
        self.assertTrue(any("GOAL STEP 0" in m for m in posted))

    def test_step0_no_ticket_id_skips_to_step4(self):
        """Step 0 with no ticket ID posts ready and advances to step 4."""
        goal = _make_goal(step=0, task="no ticket here just a question")
        result, posted, _ = _run_step(goal)
        self.assertEqual(goal.metadata["current_step"], 4)
        self.assertTrue(any("ready" in m.lower() for m in posted))

    def test_step1_show_with_grep_for(self):
        """Step 1 loads grep_for from _load_ticket and stores it in goal metadata."""
        goal = _make_goal(step=1)
        result, posted, _ = _run_step(
            goal,
            bash_returns=["show output (display)"],
            ticket_data={
                "id": "T-test-001",
                "title": "Test ticket",
                "grep_for": ["wg_cooccur", "wg_bigram"],
            },
        )
        self.assertIn("details posted", result)
        self.assertEqual(goal.metadata["current_step"], 2)
        self.assertEqual(goal.metadata.get("grep_for"), ["wg_cooccur", "wg_bigram"])
        self.assertTrue(any("GOAL STEP 1" in m for m in posted))

    def test_step1_show_no_grep_for(self):
        """Step 1 with ticket having no grep_for stores nothing, still advances."""
        goal = _make_goal(step=1)
        result, posted, _ = _run_step(
            goal,
            bash_returns=["show output"],
            ticket_data={"id": "T-test-001", "title": "No grep ticket"},
        )
        self.assertEqual(goal.metadata["current_step"], 2)
        self.assertNotIn("grep_for", goal.metadata)

    def test_step1_ticket_not_found_is_safe(self):
        """Step 1 with _load_ticket returning None doesn't crash, still advances."""
        goal = _make_goal(step=1)
        result, posted, _ = _run_step(
            goal,
            bash_returns=["show output"],
            ticket_data=None,
        )
        self.assertEqual(goal.metadata["current_step"], 2)
        self.assertNotIn("error", result.lower())

    def test_step2_grep_when_grep_for_present(self):
        """Step 2 runs grep when grep_for is in goal metadata."""
        goal = _make_goal(step=2, grep_for=["wg_cooccur"])
        grep_output = "tools/word_graph.py:42: wg_cooccur(...)"
        result, posted, _ = _run_step(goal, bash_returns=[grep_output])
        self.assertIn("grep step done", result)
        self.assertEqual(goal.metadata["current_step"], 3)
        self.assertTrue(any("GOAL STEP 2" in m for m in posted))
        self.assertTrue(any("wg_cooccur" in m for m in posted))

    def test_step2_skip_when_no_grep_for(self):
        """Step 2 skips (no bash call) when grep_for absent."""
        goal = _make_goal(step=2)  # no grep_for
        from wild_igor.igor.tools import goal_continuation as gc
        from wild_igor.igor.tools.goal_continuation import GoalContinuation

        bash_called = []

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [goal]
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
            _MT_PATH, mock_mt
        ), patch.object(
            GoalContinuation,
            "_run_bash",
            side_effect=lambda c: bash_called.append(c) or "(no)",
        ), patch.object(
            gc, "_post_to_channel"
        ):
            result = gc.run_goal_continuation()

        self.assertIn("skip", result)
        self.assertEqual(goal.metadata["current_step"], 3)
        self.assertEqual(len(bash_called), 0)

    def test_step3_ready_signal(self):
        """Step 3 posts GOAL READY and advances to step 4."""
        goal = _make_goal(step=3)
        result, posted, _ = _run_step(goal)
        self.assertIn("ready signal", result)
        self.assertEqual(goal.metadata["current_step"], 4)
        self.assertTrue(any("GOAL READY" in m for m in posted))

    def test_step3_ready_signal_mentions_grep_steps(self):
        """Step 3 after grep path reports 'Steps 0-2 complete'."""
        goal = _make_goal(step=3, grep_for=["wg_cooccur"])
        _, posted, _ = _run_step(goal)
        self.assertTrue(any("0-2" in m for m in posted))

    def test_step3_ready_signal_no_grep_path(self):
        """Step 3 without grep reports 'Steps 0-1 complete'."""
        goal = _make_goal(step=3)  # no grep_for
        _, posted, _ = _run_step(goal)
        self.assertTrue(any("0-1" in m for m in posted))

    def test_step4_lm_territory(self):
        """Step 4+ with live GOAL_READY TWM entry: no-op LLM-territory path."""
        goal = _make_goal(step=4)
        from wild_igor.igor.tools import goal_continuation as gc
        from wild_igor.igor.tools.goal_continuation import GoalContinuation

        posted = []

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [goal]
        # TWM already has a live goal_ready for this ticket — no re-emit
        mock_cortex.twm_read.return_value = [
            {"id": 1, "content_csb": "GOAL_READY|T-test-001"}
        ]
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
            _MT_PATH, mock_mt
        ), patch.object(GoalContinuation, "_run_bash"), patch.object(
            gc, "_post_to_channel", side_effect=posted.append
        ):
            result = gc.run_goal_continuation()

        self.assertIn("LLM territory", result)
        self.assertEqual(goal.metadata["current_step"], 4)  # unchanged
        self.assertEqual(len(posted), 0)
        # Live entry means twm_push must NOT be called
        mock_cortex.twm_push.assert_not_called()

    def test_step4_re_emits_goal_ready_when_twm_empty(self):
        """Step 4 re-emits GOAL_READY when TWM has no live entry (restart recovery)."""
        goal = _make_goal(step=4)
        from wild_igor.igor.tools import goal_continuation as gc
        from wild_igor.igor.tools.goal_continuation import GoalContinuation

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [goal]
        mock_cortex.twm_read.return_value = []  # TWM empty — post-restart shape
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
            _MT_PATH, mock_mt
        ), patch.object(GoalContinuation, "_run_bash"), patch.object(
            gc, "_post_to_channel"
        ):
            result = gc.run_goal_continuation()

        self.assertIn("re-emitted", result)
        self.assertIn("T-test-001", result)
        self.assertEqual(goal.metadata["current_step"], 4)  # stays at 4
        mock_cortex.twm_push.assert_called_once()
        # Verify category + content shape are what the listener expects
        kwargs = mock_cortex.twm_push.call_args.kwargs
        self.assertEqual(kwargs.get("category"), "goal_ready")
        self.assertIn("GOAL_READY|T-test-001", kwargs.get("content_csb", ""))

    def test_step4_re_emits_when_twm_has_entry_for_other_ticket(self):
        """Re-emit fires if TWM has a goal_ready for a DIFFERENT ticket."""
        goal = _make_goal(step=4)
        from wild_igor.igor.tools import goal_continuation as gc
        from wild_igor.igor.tools.goal_continuation import GoalContinuation

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [goal]
        # Entry exists but for a different ticket
        mock_cortex.twm_read.return_value = [
            {"id": 5, "content_csb": "GOAL_READY|T-some-other"}
        ]
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
            _MT_PATH, mock_mt
        ), patch.object(GoalContinuation, "_run_bash"), patch.object(
            gc, "_post_to_channel"
        ):
            result = gc.run_goal_continuation()

        self.assertIn("re-emitted", result)
        mock_cortex.twm_push.assert_called_once()

    def test_step4_skips_re_emit_when_ticket_awaiting_approval(self):
        """Step 4 must NOT re-emit GOAL_READY when ticket is awaiting_approval.
        T-scope-guard-reattempt-loop: re-emitting into an awaiting_approval
        ticket causes the SCOPE_GUARD escalation loop to repeat every 2 min.
        """
        goal = _make_goal(step=4)
        from wild_igor.igor.tools import goal_continuation as gc
        from wild_igor.igor.tools.goal_continuation import GoalContinuation

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [goal]
        mock_cortex.twm_read.return_value = []
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        fake_ticket = {"id": "T-test-001", "status": "awaiting_approval"}

        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
            _MT_PATH, mock_mt
        ), patch.object(GoalContinuation, "_run_bash"), patch.object(
            gc, "_post_to_channel"
        ), patch.object(
            GoalContinuation, "_load_ticket", return_value=fake_ticket
        ):
            result = gc.run_goal_continuation()

        self.assertIn("awaiting_approval", result)
        mock_cortex.twm_push.assert_not_called()

    def test_step4_skips_re_emit_when_ticket_blocked(self):
        """Step 4 must NOT re-emit GOAL_READY when ticket is blocked."""
        goal = _make_goal(step=4)
        from wild_igor.igor.tools import goal_continuation as gc
        from wild_igor.igor.tools.goal_continuation import GoalContinuation

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [goal]
        mock_cortex.twm_read.return_value = []
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        fake_ticket = {"id": "T-test-001", "status": "blocked"}

        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
            _MT_PATH, mock_mt
        ), patch.object(GoalContinuation, "_run_bash"), patch.object(
            gc, "_post_to_channel"
        ), patch.object(
            GoalContinuation, "_load_ticket", return_value=fake_ticket
        ):
            result = gc.run_goal_continuation()

        self.assertIn("blocked", result)
        mock_cortex.twm_push.assert_not_called()

    def test_step4_no_ticket_id_no_re_emit(self):
        """Step 4 with no ticket_id in goal: safe no-op, no TWM calls."""
        goal = _make_goal(step=4, task="no ticket here just a question")
        from wild_igor.igor.tools import goal_continuation as gc
        from wild_igor.igor.tools.goal_continuation import GoalContinuation

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [goal]
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
            _MT_PATH, mock_mt
        ), patch.object(GoalContinuation, "_run_bash"), patch.object(
            gc, "_post_to_channel"
        ):
            result = gc.run_goal_continuation()

        self.assertIn("LLM territory", result)
        mock_cortex.twm_read.assert_not_called()
        mock_cortex.twm_push.assert_not_called()

    def test_run_bash_not_truncated_at_500_chars(self):
        """_run_bash 2KB cap: a 600-char result is returned untruncated (old cap was 500)."""
        from wild_igor.igor.tools.goal_continuation import GoalContinuation

        gc_instance = GoalContinuation()
        long_output = "x" * 600

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=long_output, stderr="", returncode=0
            )
            result = gc_instance._run_bash(["some", "cmd"])

        self.assertEqual(len(result), 600)
        self.assertNotEqual(len(result), 500)


if __name__ == "__main__":
    unittest.main()

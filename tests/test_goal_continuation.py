"""
tests/test_goal_continuation.py — Unit tests for D274 goal_continuation.py.

Tests cover the step-machine logic: step 0 (claim), step 1 (show+grep_for parse),
step 2 (grep or skip), step 3 (ready signal), step 4+ (LLM territory).

No Postgres, no filesystem I/O — all external calls are mocked.

Ref: T-phase-d-canonical / Phase D ex4
"""

from __future__ import annotations

import json
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


def _run_step(goal, bash_returns=None):
    """
    Call run_goal_continuation with one goal and mock I/O.
    Returns (result_str, posted_messages).
    """
    from wild_igor.igor.tools import goal_continuation as gc

    bash_iter = iter(bash_returns or [])
    posted = []

    mock_cortex = MagicMock()
    mock_cortex.get_by_type.return_value = [goal]

    mock_mt = MagicMock()
    mock_mt.GOAL = "GOAL"

    with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
        _MT_PATH, mock_mt
    ), patch.object(
        gc, "_run_bash", side_effect=lambda _: next(bash_iter, "(no output)")
    ), patch.object(
        gc, "_post_to_channel", side_effect=posted.append
    ), patch.object(
        gc, "_flog"
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
        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
            _MT_PATH, mock_mt
        ), patch.object(gc, "_flog"):
            result = gc.run_goal_continuation()
        self.assertIn("no active goals", result)

    def test_step0_claim(self):
        """Step 0 runs cc_queue claim and advances to step 1."""
        goal = _make_goal(step=0)
        result, posted, cortex = _run_step(goal, bash_returns=["Claimed T-test-001"])
        self.assertIn("claimed", result)
        self.assertEqual(goal.metadata["current_step"], 1)
        self.assertTrue(any("GOAL STEP 0" in m for m in posted))

    def test_step0_no_ticket_id_skips_to_step4(self):
        """Step 0 with no ticket ID posts ready and advances to step 4."""
        goal = _make_goal(step=0, task="no ticket here just a question")
        result, posted, _ = _run_step(goal)
        self.assertEqual(goal.metadata["current_step"], 4)
        self.assertTrue(any("ready" in m.lower() for m in posted))

    def test_step1_show_with_grep_for(self):
        """Step 1 parses grep_for from ticket JSON and stores it in goal metadata."""
        goal = _make_goal(step=1)
        ticket_json = json.dumps(
            {
                "id": "T-test-001",
                "title": "Test ticket",
                "grep_for": ["wg_cooccur", "wg_bigram"],
            }
        )
        result, posted, _ = _run_step(goal, bash_returns=[ticket_json])
        self.assertIn("details posted", result)
        self.assertEqual(goal.metadata["current_step"], 2)
        self.assertEqual(goal.metadata.get("grep_for"), ["wg_cooccur", "wg_bigram"])
        self.assertTrue(any("GOAL STEP 1" in m for m in posted))

    def test_step1_show_no_grep_for(self):
        """Step 1 with no grep_for in ticket JSON stores nothing, still advances."""
        goal = _make_goal(step=1)
        ticket_json = json.dumps({"id": "T-test-001", "title": "No grep ticket"})
        result, posted, _ = _run_step(goal, bash_returns=[ticket_json])
        self.assertEqual(goal.metadata["current_step"], 2)
        self.assertNotIn("grep_for", goal.metadata)

    def test_step1_non_json_output_is_safe(self):
        """Step 1 with non-JSON ticket output doesn't crash, still advances."""
        goal = _make_goal(step=1)
        result, posted, _ = _run_step(goal, bash_returns=["not valid json {{{"])
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

        bash_called = []

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [goal]
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
            _MT_PATH, mock_mt
        ), patch.object(
            gc, "_run_bash", side_effect=lambda c: bash_called.append(c) or "(no)"
        ), patch.object(
            gc, "_post_to_channel"
        ), patch.object(
            gc, "_flog"
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
        """Step 4+ returns LLM territory message without posting or advancing."""
        goal = _make_goal(step=4)
        from wild_igor.igor.tools import goal_continuation as gc

        posted = []

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = [goal]
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        with patch(_CORTEX_PATH, return_value=mock_cortex), patch(
            _MT_PATH, mock_mt
        ), patch.object(gc, "_run_bash"), patch.object(
            gc, "_post_to_channel", side_effect=posted.append
        ), patch.object(
            gc, "_flog"
        ):
            result = gc.run_goal_continuation()

        self.assertIn("LLM territory", result)
        self.assertEqual(goal.metadata["current_step"], 4)  # unchanged
        self.assertEqual(len(posted), 0)


if __name__ == "__main__":
    unittest.main()

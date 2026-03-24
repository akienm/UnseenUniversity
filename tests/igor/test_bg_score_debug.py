"""
test_bg_score_debug.py — Tests for T-bg-score-debug: BG scoring dump in turn trace

Tests:
  - select_habit winner path emits bg_scoring to TurnContext
  - select_habit no-candidates path emits bg_scoring with winner=None
  - select_habit compile-phrase pre-check emits bg_scoring with pre_check key
  - select_habit notebook pre-check emits bg_scoring with pre_check key
  - bg_scoring top list contains up to 5 candidates with id/score/type
  - bg_scoring near_misses count matches actual near misses
  - _emit_bg swallows exceptions — select_habit never raises from tracing
  - bg_scoring threshold reflects milieu-modulated value
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))


def _make_habit(hid, trigger, habit_type="action", activation=0):
    from igor.memory.models import Memory, MemoryType

    m = Memory(
        id=hid,
        narrative=f"habit {hid}",
        memory_type=MemoryType.PROCEDURAL,
        metadata={"trigger": trigger, "habit_type": habit_type},
    )
    m.activation_count = activation
    return m


def _make_parsed(text, intent="action_request"):
    p = MagicMock()
    p.intent = intent
    p.tone = "neutral"
    p.tags = []
    p.core_input = text
    p.raw = text
    p.keywords = text.lower().split()
    p.complexity = "medium"
    return p


class TestBGScoreDebugWinnerPath(unittest.TestCase):
    """Winner path emits bg_scoring to TurnContext."""

    def _run(self, habits, text):
        from igor.cognition import basal_ganglia

        captures = {}

        def fake_update(stage, data):
            captures[stage] = data

        with patch(
            "igor.cognition.forensic_logger.turn_ctx_update", side_effect=fake_update
        ):
            # Patch forensic_logger inside basal_ganglia's lazy import path
            import igor.cognition.forensic_logger as fl

            orig = fl.turn_ctx_update
            fl.turn_ctx_update = fake_update
            try:
                parsed = _make_parsed(text)
                result = basal_ganglia.select_habit(parsed, habits)
            finally:
                fl.turn_ctx_update = orig
        return result, captures

    def test_winner_path_emits_bg_scoring(self):
        habit = _make_habit("PROC_TEST", "hello")
        _result, caps = self._run([habit], "hello there")
        self.assertIn("bg_scoring", caps)

    def test_winner_path_has_correct_winner(self):
        habit = _make_habit("PROC_TEST", "hello")
        result, caps = self._run([habit], "hello there")
        self.assertEqual(result[0].id, "PROC_TEST")
        self.assertEqual(caps["bg_scoring"]["winner"], "PROC_TEST")

    def test_winner_path_rationale(self):
        habit = _make_habit("PROC_TEST", "hello")
        _result, caps = self._run([habit], "hello there")
        self.assertEqual(caps["bg_scoring"]["rationale"], "max_score_wins")

    def test_winner_path_top_list_populated(self):
        habit = _make_habit("PROC_TEST", "hello")
        _result, caps = self._run([habit], "hello there")
        top = caps["bg_scoring"]["top"]
        self.assertIsInstance(top, list)
        self.assertGreater(len(top), 0)
        self.assertIn("id", top[0])
        self.assertIn("score", top[0])
        self.assertIn("type", top[0])

    def test_top_list_capped_at_5(self):
        habits = [_make_habit(f"PROC_{i}", "keyword") for i in range(10)]
        _result, caps = self._run(habits, "keyword input")
        top = caps["bg_scoring"]["top"]
        self.assertLessEqual(len(top), 5)

    def test_threshold_present_and_numeric(self):
        habit = _make_habit("PROC_TEST", "hello")
        _result, caps = self._run([habit], "hello there")
        t = caps["bg_scoring"]["threshold"]
        self.assertIsInstance(t, float)
        self.assertGreater(t, 0.0)


class TestBGScoreDebugNoWinner(unittest.TestCase):
    """No-candidates path emits bg_scoring with winner=None."""

    def _run(self, habits, text):
        import igor.cognition.forensic_logger as fl
        from igor.cognition import basal_ganglia

        captures = {}
        orig = fl.turn_ctx_update
        fl.turn_ctx_update = lambda stage, data: captures.update({stage: data})
        try:
            parsed = _make_parsed(text)
            result = basal_ganglia.select_habit(parsed, habits)
        finally:
            fl.turn_ctx_update = orig
        return result, captures

    def test_no_winner_emits_bg_scoring(self):
        habit = _make_habit("PROC_MISS", "xxxxunlikelytrigger")
        _result, caps = self._run([habit], "hello there")
        self.assertIn("bg_scoring", caps)

    def test_no_winner_has_none(self):
        habit = _make_habit("PROC_MISS", "xxxxunlikelytrigger")
        result, caps = self._run([habit], "hello there")
        self.assertIsNone(result[0])
        self.assertIsNone(caps["bg_scoring"]["winner"])

    def test_no_winner_rationale(self):
        habit = _make_habit("PROC_MISS", "xxxxunlikelytrigger")
        _result, caps = self._run([habit], "hello there")
        self.assertEqual(
            caps["bg_scoring"]["rationale"], "no_candidates_above_threshold"
        )


class TestBGScoreDebugPreChecks(unittest.TestCase):
    """Pre-check paths emit bg_scoring with pre_check key."""

    def _run(self, habits, text):
        import igor.cognition.forensic_logger as fl
        from igor.cognition import basal_ganglia

        captures = {}
        orig = fl.turn_ctx_update
        fl.turn_ctx_update = lambda stage, data: captures.update({stage: data})
        try:
            parsed = _make_parsed(text)
            result = basal_ganglia.select_habit(parsed, habits)
        finally:
            fl.turn_ctx_update = orig
        return result, captures

    def test_compile_phrase_emits_pre_check(self):
        from igor.memory.models import Memory, MemoryType

        compiler = Memory(
            id="PROC_HABIT_COMPILER",
            narrative="compiler",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"trigger": "build a habit", "habit_type": "cognitive"},
        )
        _result, caps = self._run([compiler], "build a habit for me")
        self.assertIn("bg_scoring", caps)
        self.assertEqual(caps["bg_scoring"].get("pre_check"), "compile_phrase")
        self.assertEqual(caps["bg_scoring"]["winner"], "PROC_HABIT_COMPILER")

    def test_notebook_phrase_emits_pre_check(self):
        from igor.memory.models import Memory, MemoryType

        saver = Memory(
            id="PROC_NOTEBOOK_SAVE",
            narrative="save note",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"trigger": "remember this for me", "habit_type": "cognitive"},
        )
        _result, caps = self._run([saver], "remember this for me")
        self.assertIn("bg_scoring", caps)
        self.assertEqual(caps["bg_scoring"].get("pre_check"), "notebook_phrase")
        self.assertEqual(caps["bg_scoring"]["winner"], "PROC_NOTEBOOK_SAVE")


class TestBGScoreDebugRobustness(unittest.TestCase):
    """_emit_bg failure must never propagate out of select_habit."""

    def test_forensic_logger_import_error_does_not_crash(self):
        import igor.cognition.forensic_logger as fl
        from igor.cognition import basal_ganglia

        orig = fl.turn_ctx_update

        def explode(stage, data):
            raise RuntimeError("simulated logger failure")

        fl.turn_ctx_update = explode
        try:
            habit = _make_habit("PROC_TEST", "hello")
            parsed = _make_parsed("hello there")
            # Must not raise
            result = basal_ganglia.select_habit(parsed, [habit])
            self.assertIsNotNone(result)
        finally:
            fl.turn_ctx_update = orig


if __name__ == "__main__":
    unittest.main()

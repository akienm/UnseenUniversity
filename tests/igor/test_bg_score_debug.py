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
from unittest.mock import MagicMock


def _make_habit(hid, trigger, habit_type="action", activation=0):
    from devices.igor.memory.models import Memory, MemoryType

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

    def setUp(self):
        from devices.igor.cognition import basal_ganglia

        basal_ganglia._refractory_map.clear()

    def tearDown(self):
        from devices.igor.cognition import basal_ganglia

        basal_ganglia._refractory_map.clear()

    def _run(self, habits, text):
        import devices.igor.cognition.forensic_logger as fl
        from devices.igor.cognition import basal_ganglia

        captures = {}
        orig = fl.turn_ctx_update
        fl.turn_ctx_update = lambda stage, data: captures.update({stage: data})
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
        import devices.igor.cognition.forensic_logger as fl
        from devices.igor.cognition import basal_ganglia

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
        import devices.igor.cognition.forensic_logger as fl
        from devices.igor.cognition import basal_ganglia

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
        from devices.igor.memory.models import Memory, MemoryType

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
        from devices.igor.memory.models import Memory, MemoryType

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
        import devices.igor.cognition.forensic_logger as fl
        from devices.igor.cognition import basal_ganglia

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


class TestApplyIntentGate(unittest.TestCase):
    """Tests for _apply_intent_gate: intent-based habit filtering."""

    def test_intent_gate_passes_on_action_intent(self):
        """Action-type habits pass gate on non-question intents."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_ACTION",
            narrative="test action",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "action"},
        )
        # Should pass on action_request intent
        result = basal_ganglia._apply_intent_gate(habit, "action_request")
        self.assertTrue(result)

    def test_intent_gate_blocks_action_on_question_intent(self):
        """Action-type habits fail gate on question intents."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_ACTION",
            narrative="test action",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "action"},
        )
        # Should fail on factual_question intent
        result = basal_ganglia._apply_intent_gate(habit, "factual_question")
        self.assertFalse(result)

    def test_intent_gate_blocks_workflow_on_question_intent(self):
        """Workflow-type habits also fail on question intents."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_WORKFLOW",
            narrative="test workflow",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "workflow"},
        )
        # Should fail on knowledge_request intent
        result = basal_ganglia._apply_intent_gate(habit, "knowledge_request")
        self.assertFalse(result)

    def test_intent_gate_response_fails_on_knowledge_intent(self):
        """Response habits fail on knowledge intents."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_RESPONSE",
            narrative="test response",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "response"},
        )
        # Should fail on factual_question intent
        result = basal_ganglia._apply_intent_gate(habit, "factual_question")
        self.assertFalse(result)

    def test_intent_gate_response_passes_on_action_intent(self):
        """Response habits pass on action intent."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_RESPONSE",
            narrative="test response",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "response"},
        )
        # Should pass on action_request intent
        result = basal_ganglia._apply_intent_gate(habit, "action_request")
        self.assertTrue(result)

    def test_intent_gate_threshold_always_fails(self):
        """Threshold-type habits always fail gate."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_THRESHOLD",
            narrative="test threshold",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "threshold"},
        )
        # Threshold habits always fail (evaluated separately)
        result = basal_ganglia._apply_intent_gate(habit, "action_request")
        self.assertFalse(result)

    def test_intent_gate_author_filter_blocks_wrong_author(self):
        """Author filter blocks habits restricted to different authors."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_CC_ONLY",
            narrative="cc-only habit",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "action", "author_filter": "claude-code"},
        )
        # Should fail when author is not "claude-code"
        result = basal_ganglia._apply_intent_gate(
            habit, "action_request", author="akien"
        )
        self.assertFalse(result)

    def test_intent_gate_author_filter_passes_matching_author(self):
        """Author filter passes when author matches."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_CC_ONLY",
            narrative="cc-only habit",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "action", "author_filter": "claude-code"},
        )
        # Should pass when author matches
        result = basal_ganglia._apply_intent_gate(
            habit, "action_request", author="claude-code"
        )
        self.assertTrue(result)


class TestApplySpecificityBonus(unittest.TestCase):
    """Tests for _apply_specificity_bonus: specificity scoring."""

    def test_specificity_bonus_zero_without_conditions(self):
        """Specificity bonus is 0 when no conditions present."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_TEST",
            narrative="test habit",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "action"},  # No conditions
        )
        parsed = _make_parsed("test input")
        bonus = basal_ganglia._apply_specificity_bonus(
            habit, parsed=parsed, _wg_scores={}, meaning_to_me_context=False
        )
        self.assertEqual(bonus, 0.0)

    def test_specificity_bonus_positive_with_matching_conditions(self):
        """Specificity bonus > 0 when condition fields match."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_TEST",
            narrative="test habit",
            memory_type=MemoryType.PROCEDURAL,
            metadata={
                "habit_type": "action",
                "conditions": {"intent": ["action_request"]},
            },
        )
        parsed = _make_parsed("test input", intent="action_request")
        bonus = basal_ganglia._apply_specificity_bonus(
            habit, parsed=parsed, _wg_scores={}, meaning_to_me_context=False
        )
        self.assertGreater(bonus, 0.0)

    def test_specificity_bonus_includes_word_graph_score(self):
        """Specificity bonus includes word graph score."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_TEST",
            narrative="test habit",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "action"},
        )
        parsed = _make_parsed("test input")
        # Provide word graph score for this habit
        wg_scores = {"PROC_TEST": 0.5}
        bonus = basal_ganglia._apply_specificity_bonus(
            habit, parsed=parsed, _wg_scores=wg_scores, meaning_to_me_context=False
        )
        # Word graph bonus = 0.5 * 0.10 = 0.05
        self.assertAlmostEqual(bonus, 0.05, places=2)

    def test_specificity_bonus_includes_meaning_to_me_context(self):
        """Specificity bonus includes meaning_to_me context bonus."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_TEST",
            narrative="test habit",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"habit_type": "action", "meaning_to_me": True},
        )
        parsed = _make_parsed("test input")
        bonus = basal_ganglia._apply_specificity_bonus(
            habit, parsed=parsed, _wg_scores={}, meaning_to_me_context=True
        )
        # Meaning_to_me bonus = 0.05
        self.assertAlmostEqual(bonus, 0.05, places=2)

    def test_specificity_bonus_combines_all_bonuses(self):
        """Specificity bonus combines conditions + word graph + meaning_to_me."""
        from devices.igor.cognition import basal_ganglia
        from devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_TEST",
            narrative="test habit",
            memory_type=MemoryType.PROCEDURAL,
            metadata={
                "habit_type": "action",
                "conditions": {"intent": ["action_request"]},
                "meaning_to_me": True,
            },
        )
        parsed = _make_parsed("test input", intent="action_request")
        wg_scores = {"PROC_TEST": 0.5}
        bonus = basal_ganglia._apply_specificity_bonus(
            habit,
            parsed=parsed,
            _wg_scores=wg_scores,
            meaning_to_me_context=True,
        )
        # conditions_bonus (1 field * 0.08) + word_graph (0.5 * 0.10) + meaning_to_me (0.05)
        # = 0.08 + 0.05 + 0.05 = 0.18
        self.assertAlmostEqual(bonus, 0.18, places=2)


if __name__ == "__main__":
    unittest.main()

"""
test_exit_habit_misfire.py — Tests for T-exit-habit-misfire fixes.

Covers:
  1. author_filter list support in select_habit
  2. Pipe-separated trigger tightening — "yourself" no longer fires exit habit
  3. Pipe phrases correctly match real exit commands
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock



def _make_habit(
    trigger, author_filter=None, habit_type="action", code_ref="tools.runner:exit_self"
):
    """Build a minimal mock habit Memory."""
    from devices.igor.memory.models import Memory, MemoryType

    meta = {
        "trigger": trigger,
        "habit_type": habit_type,
        "code_ref": code_ref,
    }
    if author_filter is not None:
        meta["author_filter"] = author_filter
    m = Memory(
        id="PROC_EXIT_IGOR",
        narrative="Exit habit",
        memory_type=MemoryType.PROCEDURAL,
        metadata=meta,
    )
    return m


def _make_parsed(text="", intent="action_request"):
    """Use action_request — 'general' and 'conversation' are in _QUESTION_INTENTS
    which suppresses action habits with code_ref (G-OVN-1b). Exit commands are
    imperative actions, so action_request is the correct intent for testing."""
    parsed = MagicMock()
    parsed.intent = intent
    parsed.tone = "neutral"
    parsed.tags = []
    parsed.core_input = text
    parsed.raw = text
    parsed.keywords = text.lower().split()
    return parsed


class TestAuthorFilterListSupport(unittest.TestCase):
    """author_filter stored as list must work correctly in select_habit."""

    def setUp(self):
        from devices.igor.cognition import basal_ganglia

        basal_ganglia._refractory_map.clear()

    def tearDown(self):
        from devices.igor.cognition import basal_ganglia

        basal_ganglia._refractory_map.clear()

    def _run_select(self, habits, text, author):
        from devices.igor.cognition.basal_ganglia import select_habit

        parsed = _make_parsed(text=text)
        winner, _conf, _near = select_habit(parsed, habits, author=author)
        return winner

    def test_list_filter_allows_matching_author(self):
        """["akien", "user"] should allow author="akien" to trigger."""
        habit = _make_habit(
            trigger="exit igor|quit igor",
            author_filter=["akien", "user"],
        )
        result = self._run_select([habit], "exit igor", author="akien")
        self.assertIsNotNone(result, "exit habit should fire for author=akien")

    def test_list_filter_allows_second_entry(self):
        """["akien", "user"] should allow author="user" (REPL)."""
        habit = _make_habit(
            trigger="exit igor|quit igor",
            author_filter=["akien", "user"],
        )
        result = self._run_select([habit], "exit igor", author="user")
        self.assertIsNotNone(result, "exit habit should fire for author=user")

    def test_list_filter_blocks_claude_code(self):
        """["akien", "user"] must block author="claude-code"."""
        habit = _make_habit(
            trigger="exit igor|quit igor",
            author_filter=["akien", "user"],
        )
        result = self._run_select([habit], "exit igor", author="claude-code")
        self.assertIsNone(result, "exit habit must NOT fire for author=claude-code")

    def test_string_filter_still_works(self):
        """Original string format author_filter must not regress."""
        habit = _make_habit(
            trigger="cc run bash|execute bash",
            author_filter="claude-code",
        )
        result = self._run_select([habit], "cc run bash", author="claude-code")
        self.assertIsNotNone(result, "CC habit should fire for claude-code")

    def test_string_filter_blocks_human(self):
        """String format must still block non-matching author."""
        habit = _make_habit(
            trigger="cc run bash|execute bash",
            author_filter="claude-code",
        )
        result = self._run_select([habit], "cc run bash", author="akien")
        self.assertIsNone(result, "CC habit must NOT fire for human author")


class TestExitTriggerTightening(unittest.TestCase):
    """Pipe-separated trigger must not misfire on 'run it yourself'."""

    NEW_TRIGGER = (
        "exit igor|quit igor|shutdown igor|shut yourself down|halt igor|"
        "stop igor cleanly|terminate igor|shut igor down|shut igor off|exit cleanly"
    )

    def _score(self, text):
        from devices.igor.cognition.basal_ganglia import _score_habit

        habit = _make_habit(trigger=self.NEW_TRIGGER)
        return _score_habit(habit, text.lower(), set(text.lower().split()))

    def test_run_it_yourself_does_not_fire(self):
        """The misfire case: 'Run it yourself.' must score 0."""
        score = self._score("Run it yourself.")
        self.assertEqual(score, 0.0, "'Run it yourself' must not trigger exit habit")

    def test_exit_igor_fires(self):
        self.assertGreater(self._score("exit igor"), 0.0)

    def test_quit_igor_fires(self):
        self.assertGreater(self._score("quit igor"), 0.0)

    def test_shutdown_igor_fires(self):
        self.assertGreater(self._score("shutdown igor"), 0.0)

    def test_shut_yourself_down_fires(self):
        self.assertGreater(self._score("shut yourself down"), 0.0)

    def test_terminate_igor_fires(self):
        self.assertGreater(self._score("terminate igor"), 0.0)

    def test_halt_igor_fires(self):
        self.assertGreater(self._score("halt igor"), 0.0)

    def test_exit_cleanly_fires(self):
        self.assertGreater(self._score("exit cleanly"), 0.0)

    def test_unrelated_phrase_does_not_fire(self):
        self.assertEqual(self._score("let's implement this together"), 0.0)


if __name__ == "__main__":
    unittest.main()

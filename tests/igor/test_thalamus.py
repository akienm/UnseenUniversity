"""
test_thalamus.py — Tests for thalamus intent classification.

Covers: relational-pronoun guard, graph-weight hook stub, lexical cascade.
No network calls, no DB — thalamus._classify_intent is pure Python.
"""

import sys
from pathlib import Path
import unittest


from unseen_university.devices.igor.cognition.thalamus import _classify_intent, _graph_weight_intent_hint


class TestRelationalPronounGuard(unittest.TestCase):
    """Peer/vision inputs must not be misclassified as code_task or action_request."""

    def _intent(self, text: str) -> str:
        from unseen_university.devices.igor.cognition.thalamus import _extract_keywords
        kw = _extract_keywords(text)
        return _classify_intent(text, kw)

    def test_peer_plus_vision_returns_conversation(self):
        """Root-cause case: CC_FIND_TICKETS misfire f5bff77c."""
        result = self._intent("you and I work as peers, drive Claude's process")
        self.assertEqual(result, "conversation")

    def test_peer_plus_end_state_returns_conversation(self):
        result = self._intent("you and me working together toward our end state")
        self.assertEqual(result, "conversation")

    def test_peer_only_does_not_force_conversation(self):
        """Peer signal alone without vision signal should not override."""
        result = self._intent("we work on this code")
        # Should be code_task or general — NOT forced to conversation by guard alone
        self.assertNotEqual(result, "conversation")

    def test_vision_only_does_not_force_conversation(self):
        """Vision signal alone without peer signal falls through to cascade."""
        result = self._intent("implement our end goal now")
        # implement → code_task; no peer signal to guard
        self.assertEqual(result, "code_task")

    def test_graph_weight_stub_returns_none(self):
        """Stub must return None — never overrides until wired."""
        self.assertIsNone(_graph_weight_intent_hint("anything", []))
        self.assertIsNone(_graph_weight_intent_hint("", []))


class TestLexicalCascadeUnchanged(unittest.TestCase):
    """Verify existing intent patterns still work after the guard insertion."""

    def _intent(self, text: str) -> str:
        from unseen_university.devices.igor.cognition.thalamus import _extract_keywords
        kw = _extract_keywords(text)
        return _classify_intent(text, kw)

    def test_command(self):
        self.assertEqual(self._intent("/status"), "command")

    def test_greeting(self):
        self.assertEqual(self._intent("hey there"), "greeting")

    def test_code_task(self):
        self.assertEqual(self._intent("implement a function that sorts"), "code_task")

    def test_memory_instruction(self):
        self.assertEqual(self._intent("remember that I prefer dark mode"), "memory_instruction")

    def test_analysis_task(self):
        self.assertEqual(self._intent("analyze the patterns in this data"), "analysis_task")

    def test_general_fallthrough(self):
        result = self._intent("interesting")
        self.assertEqual(result, "conversation")


if __name__ == "__main__":
    unittest.main()

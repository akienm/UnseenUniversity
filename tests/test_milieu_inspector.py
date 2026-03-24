"""
test_milieu_inspector.py — Tests for T-milieu-inspector: get_milieu_state tool

Tests:
  - _emotion_label() neutral zone (near-zero VAD)
  - _emotion_label() engaged/excited (positive valence + arousal)
  - _emotion_label() content/calm (positive valence, low arousal)
  - _emotion_label() stressed/overwhelmed (negative valence, high arousal, low dominance)
  - _emotion_label() anxious/activated (negative valence, high arousal, positive dominance)
  - _emotion_label() alert/focused (neutral valence, high arousal, positive dominance)
  - _get_milieu_state() returns "not initialized" when milieu singleton is None
  - _get_milieu_state() returns formatted snapshot with required fields when milieu is live
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))

from igor.tools.metrics import _emotion_label, _get_milieu_state


class TestEmotionLabel(unittest.TestCase):
    def test_neutral(self):
        self.assertEqual(_emotion_label(0.0, 0.0, 0.0), "neutral")
        self.assertEqual(_emotion_label(0.05, -0.05, 0.1), "neutral")

    def test_engaged_excited(self):
        self.assertEqual(_emotion_label(0.5, 0.5, 0.3), "engaged/excited")

    def test_content_calm(self):
        self.assertEqual(_emotion_label(0.4, -0.3, 0.2), "content/calm")

    def test_positive_steady(self):
        self.assertEqual(_emotion_label(0.15, 0.05, 0.3), "positive/steady")

    def test_stressed_overwhelmed(self):
        result = _emotion_label(-0.4, 0.5, -0.2)
        self.assertEqual(result, "stressed/overwhelmed")

    def test_anxious_activated(self):
        result = _emotion_label(-0.4, 0.5, 0.2)
        self.assertEqual(result, "anxious/activated")

    def test_alert_focused(self):
        result = _emotion_label(0.05, 0.4, 0.5)
        self.assertEqual(result, "alert/focused")

    def test_restless_unsettled(self):
        result = _emotion_label(0.05, 0.4, 0.0)
        self.assertEqual(result, "restless/unsettled")

    def test_tired_inactive(self):
        result = _emotion_label(0.05, -0.3, 0.1)
        self.assertEqual(result, "tired/inactive")


class TestGetMilieuState(unittest.TestCase):
    def test_not_initialized(self):
        with patch("igor.cognition.milieu.get", return_value=None):
            result = _get_milieu_state()
        self.assertIn("not initialized", result)

    def test_returns_snapshot(self):
        mock_state = MagicMock()
        mock_state.valence = 0.2
        mock_state.arousal = 0.1
        mock_state.dominance = 0.3
        mock_state.tick = 42
        mock_state.last_update = 1000000000.0

        mock_milieu = MagicMock()
        mock_milieu.get_state.return_value = mock_state
        mock_milieu.gradient.return_value = 0.01
        mock_milieu.session_histogram.return_value = {
            "session_character": "focused",
            "sample_count": 10,
        }

        with patch("igor.cognition.milieu.get", return_value=mock_milieu):
            result = _get_milieu_state()

        self.assertIn("MILIEU STATE", result)
        self.assertIn("valence", result)
        self.assertIn("arousal", result)
        self.assertIn("dominance", result)
        self.assertIn("emotion", result)
        self.assertIn("focused", result)
        self.assertIn("tick=42", result)

    def test_exception_handled(self):
        with patch("igor.cognition.milieu.get", side_effect=RuntimeError("boom")):
            result = _get_milieu_state()
        self.assertIn("Error reading milieu state", result)


if __name__ == "__main__":
    unittest.main()

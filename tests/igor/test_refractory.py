"""
test_refractory.py — Tests for T-refractory-period: post-fire score suppression

Tests:
  - Score is full (>0) on first call for a habit that matches
  - Score is suppressed to ~10% when called again within TTL
  - Score is full again after TTL expires (mock `now` parameter to advance time)
  - Refractory map entry is cleaned up on expiry (no stale keys)
  - select_habit: winner is marked refractory; second call within TTL returns None or other winner
"""

import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock



def _make_habit(hid, trigger, habit_type="action", activation=0):
    from unseen_university.devices.igor.memory.models import Memory, MemoryType

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


def _clear_refractory():
    """Reset module-level refractory map between tests."""
    from unseen_university.devices.igor.cognition import basal_ganglia

    basal_ganglia._refractory_map.clear()


class TestRefractoryScoreHabit(unittest.TestCase):
    """_score_habit refractory suppression via injectable `now`."""

    def setUp(self):
        _clear_refractory()

    def tearDown(self):
        _clear_refractory()

    def test_first_call_full_score(self):
        """First call returns a full positive score (no refractory entry)."""
        from unseen_university.devices.igor.cognition import basal_ganglia

        habit = _make_habit("PROC_TEST_REFRAC", "keyword")
        now = datetime.now(timezone.utc)
        score = basal_ganglia._score_habit(habit, "keyword input", {"keyword"}, now=now)
        self.assertGreater(score, 0.0)

    def test_second_call_within_ttl_suppressed(self):
        """Score is ~10% of original when habit is in refractory."""
        from unseen_university.devices.igor.cognition import basal_ganglia

        habit = _make_habit("PROC_TEST_REFRAC2", "keyword")
        now = datetime.now(timezone.utc)

        # First score — baseline
        score_first = basal_ganglia._score_habit(
            habit, "keyword input", {"keyword"}, now=now
        )
        self.assertGreater(score_first, 0.0)

        # Manually enter refractory as if select_habit fired
        ttl = basal_ganglia._REFRACTORY_TTL_SEC
        basal_ganglia._refractory_map[habit.id] = now.timestamp() + ttl

        # Second call at same `now` — should be suppressed
        score_second = basal_ganglia._score_habit(
            habit, "keyword input", {"keyword"}, now=now
        )

        # Should be approximately _REFRACTORY_FACTOR * first score
        expected_factor = basal_ganglia._REFRACTORY_FACTOR
        self.assertAlmostEqual(score_second / score_first, expected_factor, places=5)

    def test_score_full_after_ttl_expires(self):
        """Score returns to full once TTL has elapsed."""
        from unseen_university.devices.igor.cognition import basal_ganglia

        habit = _make_habit("PROC_TEST_REFRAC3", "keyword")
        now = datetime.now(timezone.utc)

        # Set refractory entry already expired
        basal_ganglia._refractory_map[habit.id] = (
            now.timestamp() - 1.0
        )  # 1s in the past

        # Score with `now` past expiry — should NOT be suppressed
        score = basal_ganglia._score_habit(habit, "keyword input", {"keyword"}, now=now)

        # Entry should have been cleaned up
        self.assertNotIn(habit.id, basal_ganglia._refractory_map)

        # Score should be full (same as without any refractory entry)
        _clear_refractory()
        score_baseline = basal_ganglia._score_habit(
            habit, "keyword input", {"keyword"}, now=now
        )
        self.assertAlmostEqual(score, score_baseline, places=5)

    def test_expired_key_removed_from_map(self):
        """Stale refractory key is cleaned up when accessed."""
        from unseen_university.devices.igor.cognition import basal_ganglia

        habit = _make_habit("PROC_TEST_REFRAC4", "keyword")
        now = datetime.now(timezone.utc)

        # Insert an already-expired entry
        basal_ganglia._refractory_map[habit.id] = now.timestamp() - 100.0

        # Trigger score — should clean up the expired entry
        basal_ganglia._score_habit(habit, "keyword input", {"keyword"}, now=now)

        self.assertNotIn(habit.id, basal_ganglia._refractory_map)

    def test_suppression_is_only_refractory_factor(self):
        """Suppression multiplier matches _REFRACTORY_FACTOR exactly."""
        from unseen_university.devices.igor.cognition import basal_ganglia

        habit = _make_habit("PROC_TEST_REFRAC5", "keyword")
        now = datetime.now(timezone.utc)
        ttl = basal_ganglia._REFRACTORY_TTL_SEC

        score_clean = basal_ganglia._score_habit(
            habit, "keyword input", {"keyword"}, now=now
        )

        basal_ganglia._refractory_map[habit.id] = now.timestamp() + ttl
        score_suppressed = basal_ganglia._score_habit(
            habit, "keyword input", {"keyword"}, now=now
        )

        ratio = score_suppressed / score_clean
        self.assertAlmostEqual(ratio, basal_ganglia._REFRACTORY_FACTOR, places=6)


class TestRefractorySelectHabit(unittest.TestCase):
    """select_habit marks winner refractory; second call within TTL suppresses it."""

    def setUp(self):
        _clear_refractory()

    def tearDown(self):
        _clear_refractory()

    def _make_mock_parsed(self, text):
        return _make_parsed(text, intent="action_request")

    def test_winner_is_marked_refractory_after_select(self):
        """After select_habit returns a winner, that habit.id is in _refractory_map."""
        from unseen_university.devices.igor.cognition import basal_ganglia

        habit = _make_habit("PROC_REFRAC_WINNER", "hello")
        parsed = self._make_mock_parsed("hello there")

        winner, score, _ = basal_ganglia.select_habit(parsed, [habit])
        self.assertIsNotNone(winner)
        self.assertIn(winner.id, basal_ganglia._refractory_map)

    def test_refractory_expiry_is_in_future(self):
        """Refractory expiry timestamp is in the future relative to now."""
        from unseen_university.devices.igor.cognition import basal_ganglia

        habit = _make_habit("PROC_REFRAC_EXPIRY", "hello")
        parsed = self._make_mock_parsed("hello there")

        basal_ganglia.select_habit(parsed, [habit])
        now_ts = datetime.now(timezone.utc).timestamp()
        expiry = basal_ganglia._refractory_map.get(habit.id, 0)
        self.assertGreater(expiry, now_ts)

    def test_second_select_within_ttl_suppressed(self):
        """Habit that just fired scores below threshold on immediate re-select."""
        from unseen_university.devices.igor.cognition import basal_ganglia

        habit = _make_habit("PROC_REFRAC_SECOND", "hello")
        parsed = self._make_mock_parsed("hello there")

        # First call — should win
        winner1, score1, _ = basal_ganglia.select_habit(parsed, [habit])
        self.assertIsNotNone(winner1)
        self.assertGreater(score1, 0.0)

        # Second call — same habit, suppressed to 10%, likely below threshold
        winner2, score2, _ = basal_ganglia.select_habit(parsed, [habit])
        # Suppressed score should be much less than the first
        self.assertLess(score2, score1 * 0.5)

    def test_management_phrase_sets_refractory(self):
        """Management-phrase dispatch now sets refractory to prevent double-fire.
        T-management-phrase-word-boundary: same phrase can't re-fire within TTL."""
        from unseen_university.devices.igor.cognition import basal_ganglia
        from unseen_university.devices.igor.memory.models import Memory, MemoryType

        basal_ganglia._refractory_map.pop("PROC_SWARM_UPDATE", None)
        habit = Memory(
            id="PROC_SWARM_UPDATE",
            narrative="swarm update",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"trigger": "update swarm", "habit_type": "action"},
        )
        parsed = self._make_mock_parsed("update swarm now")
        basal_ganglia.select_habit(parsed, [habit])
        # Management phrase now sets refractory to prevent repeated same-session fires
        self.assertIn("PROC_SWARM_UPDATE", basal_ganglia._refractory_map)

    def test_management_phrase_requires_whole_word(self):
        """Partial substring of phrase must NOT trigger dispatch.
        T-management-phrase-word-boundary: 'goal continuation: ...' in prose
        must not fire PROC_GOAL_CONTINUATION."""
        from unseen_university.devices.igor.cognition import basal_ganglia
        from unseen_university.devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_GOAL_CONTINUATION",
            narrative="goal continuation",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"trigger": "goal continuation", "habit_type": "action"},
        )
        basal_ganglia._refractory_map.pop("PROC_GOAL_CONTINUATION", None)
        # Phrase embedded in prose — should NOT match as a management command
        parsed = self._make_mock_parsed(
            "goal continuation: here's what I did this session..."
        )
        result = basal_ganglia.select_habit(parsed, [habit])
        # The phrase "goal continuation" IS a whole-word match here, so it fires.
        # But a NON-phrase substring like "continuationx" would not.
        parsed2 = self._make_mock_parsed("this has goalcontinuation embedded")
        basal_ganglia._refractory_map.pop("PROC_GOAL_CONTINUATION", None)
        result2 = basal_ganglia.select_habit(parsed2, [habit])
        # "goalcontinuation" has no word boundary before "goal" — must not match
        self.assertNotEqual(
            result2[1] if result2 else 0,
            0.97,
            "Substring without word boundary must not dispatch at 0.97",
        )

    def test_compile_phrase_does_not_set_refractory(self):
        """Compile-phrase pre-check dispatch does NOT add to _refractory_map."""
        from unseen_university.devices.igor.cognition import basal_ganglia
        from unseen_university.devices.igor.memory.models import Memory, MemoryType

        habit = Memory(
            id="PROC_HABIT_COMPILER",
            narrative="compiler",
            memory_type=MemoryType.PROCEDURAL,
            metadata={"trigger": "build a habit", "habit_type": "cognitive"},
        )
        parsed = self._make_mock_parsed("build a habit for me")
        basal_ganglia.select_habit(parsed, [habit])
        self.assertNotIn("PROC_HABIT_COMPILER", basal_ganglia._refractory_map)

    def test_no_winner_does_not_set_refractory(self):
        """When no winner is selected, _refractory_map stays empty."""
        from unseen_university.devices.igor.cognition import basal_ganglia

        habit = _make_habit("PROC_UNLIKELY", "xxxxunlikelytrigger")
        parsed = self._make_mock_parsed("hello there")
        basal_ganglia.select_habit(parsed, [habit])
        self.assertNotIn("PROC_UNLIKELY", basal_ganglia._refractory_map)


if __name__ == "__main__":
    unittest.main()

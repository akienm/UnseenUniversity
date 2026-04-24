"""T-inhibitor-escalate-guard — per-thread coherence-inhibitor fire counter.

Pass-2 Area 2 P1.9: when response_coherence_inhibitor fires repeatedly on
the same thread, the correction-loop risks runaway oscillation. This test
pins the N=3-per-window escalate guard.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wild_igor.igor.cognition import response_coherence_inhibitor as rci


@pytest.fixture(autouse=True)
def _reset_fires():
    rci._reset_inhibitor_fires()
    yield
    rci._reset_inhibitor_fires()


class TestRecordFire:
    def test_first_fire_does_not_escalate(self):
        should, count = rci._record_fire_and_should_escalate("thread-A")
        assert should is False
        assert count == 1

    def test_escalates_at_N(self):
        for _ in range(rci._INHIBITOR_ESCALATE_N - 1):
            should, _ = rci._record_fire_and_should_escalate("thread-A")
            assert should is False
        should, count = rci._record_fire_and_should_escalate("thread-A")
        assert should is True
        assert count == rci._INHIBITOR_ESCALATE_N

    def test_per_thread_isolation(self):
        for _ in range(rci._INHIBITOR_ESCALATE_N):
            rci._record_fire_and_should_escalate("thread-A")
        should, count = rci._record_fire_and_should_escalate("thread-B")
        assert should is False
        assert count == 1

    def test_empty_thread_id_treated_as_placeholder(self):
        should, count = rci._record_fire_and_should_escalate("")
        assert should is False
        assert count == 1


class TestCheckCoherenceStuckPath:
    def _cortex_stub(self):
        c = MagicMock()
        c.write_ring = MagicMock()
        c.twm_push = MagicMock()
        return c

    def _fire_until_stuck(self, cortex):
        """Fire check_coherence N times with clearly-incoherent prompt/response."""
        # Force Nth call via the counter directly, but exercise the public path
        prompt = "graph memory retrieval search context lookup anchor node"
        response = (
            "watermelon zebra helicopter spaghetti volcano pineapple "
            "ornithopter xylophone quintessential"
        )
        # Pre-load N-1 fires so the Nth public call triggers stuck
        for _ in range(rci._INHIBITOR_ESCALATE_N - 1):
            rci._record_fire_and_should_escalate("test-thread")
        return rci.check_coherence(
            cortex, prompt, response, turn_id="t1", thread_id="test-thread"
        )

    def test_stuck_path_returns_stuck_reason(self):
        c = self._cortex_stub()
        with patch("wild_igor.igor.tools.channel_post.post_to_channel") as mock_post:
            result = self._fire_until_stuck(c)
        assert result["reason"] == "stuck_escalated"
        assert result["fire_count"] == rci._INHIBITOR_ESCALATE_N
        # Ring + TWM pushes should have been SKIPPED on stuck path
        c.write_ring.assert_not_called()
        c.twm_push.assert_not_called()
        # Channel post should fire with the STUCK message
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "COHERENCE_INHIBITOR_STUCK" in args[0]

    def test_non_stuck_path_still_writes_ring_and_twm(self):
        c = self._cortex_stub()
        prompt = "graph memory retrieval search context lookup anchor node"
        response = (
            "watermelon zebra helicopter spaghetti volcano pineapple "
            "ornithopter xylophone quintessential"
        )
        result = rci.check_coherence(
            c, prompt, response, turn_id="t1", thread_id="fresh-thread"
        )
        assert result["flagged"] is True
        assert result["reason"] == "below_threshold"
        c.write_ring.assert_called_once()
        c.twm_push.assert_called_once()

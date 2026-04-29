"""
test_approach_frame_audit.py — T-igor-self-audit-approach-frame

Tests for the periodic night-time approach-frame audit source.
"""

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.approach_frame_audit import (  # noqa: E402
    AUDIT_WINDOW_END,
    AUDIT_WINDOW_START,
    DEFAULT_COOLDOWN_DAYS,
    ApproachFrameAuditSource,
    _in_audit_window,
    _score_avoidance,
)
from wild_igor.igor.memory.models import Memory, MemoryType  # noqa: E402


class TestAuditWindow:
    def test_22_is_in_window(self):
        assert _in_audit_window(22) is True

    def test_23_is_in_window(self):
        assert _in_audit_window(23) is True

    def test_0_is_in_window(self):
        assert _in_audit_window(0) is True

    def test_6_is_in_window(self):
        assert _in_audit_window(6) is True

    def test_7_is_not_in_window(self):
        assert _in_audit_window(7) is False

    def test_12_is_not_in_window(self):
        assert _in_audit_window(12) is False

    def test_21_is_not_in_window(self):
        assert _in_audit_window(21) is False


class TestScoreAvoidance:
    def test_pure_avoidance_scores_high(self):
        hits, score = _score_avoidance(
            "do not write to decisions_log; never bypass /decided"
        )
        assert hits == 2
        assert score > 0.2

    def test_approach_framed_scores_zero(self):
        hits, score = _score_avoidance(
            "Always go through /decided to write the decisions log"
        )
        assert hits == 0
        assert score == 0.0

    def test_mixed_partial_score(self):
        hits, score = _score_avoidance(
            "Always stage files specifically by name. Never use git add -A."
        )
        assert hits == 1
        assert 0.05 < score < 0.15

    def test_empty_returns_zero(self):
        assert _score_avoidance("") == (0, 0.0)

    def test_none_returns_zero(self):
        assert _score_avoidance(None) == (0, 0.0)

    def test_normalizes_by_length(self):
        short_hits, short_score = _score_avoidance("don't")
        long_hits, long_score = _score_avoidance("don't " + "word " * 100)
        assert short_hits == long_hits == 1
        assert short_score > long_score


class TestApproachFrameAuditSource:
    def _make_cortex(self, memories_by_type=None):
        cortex = MagicMock()
        cortex.twm_push.return_value = 1

        def get_by_type(memory_type, limit=None, order_by="timestamp"):
            return (memories_by_type or {}).get(memory_type, [])

        cortex.get_by_type.side_effect = get_by_type
        stored_mem = MagicMock()
        stored_mem.id = "stored-mem-id"
        cortex.store.return_value = stored_mem
        return cortex

    def _make_memory(
        self, mem_id, narrative, mtype=MemoryType.PROCEDURAL, metadata=None
    ):
        return Memory(
            id=mem_id,
            narrative=narrative,
            memory_type=mtype,
            metadata=metadata or {},
        )

    def test_does_not_fire_outside_window(self):
        src = ApproachFrameAuditSource()
        cortex = self._make_cortex()
        with patch("wild_igor.igor.cognition.approach_frame_audit.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 14
            mock_dt.now.return_value = mock_now
            assert src.push(cortex) == []

    def test_disabled_by_env_var(self):
        src = ApproachFrameAuditSource()
        cortex = self._make_cortex()
        with patch.dict(os.environ, {"IGOR_APPROACH_FRAME_AUDIT": "false"}):
            assert src.push(cortex) == []

    def test_does_not_fire_during_cooldown(self):
        src = ApproachFrameAuditSource()
        src._last_audit_ts = time.monotonic() - 3600  # 1h ago, well under 3 days
        src._last_check_ts = 0
        cortex = self._make_cortex()
        with patch("wild_igor.igor.cognition.approach_frame_audit.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 23
            mock_dt.now.return_value = mock_now
            assert src.push(cortex) == []

    def test_fires_after_cooldown(self):
        src = ApproachFrameAuditSource()
        # 4 days ago — past 3-day cooldown
        src._last_audit_ts = time.monotonic() - (DEFAULT_COOLDOWN_DAYS + 1) * 86400
        src._last_check_ts = 0
        memories = {
            MemoryType.PROCEDURAL: [
                self._make_memory(
                    "mem-bad",
                    "do not write to the decisions log; never bypass the proper path",
                ),
            ],
            MemoryType.CORE_PATTERN: [],
        }
        cortex = self._make_cortex(memories)
        from datetime import datetime as real_dt, timezone as real_tz

        with patch("wild_igor.igor.cognition.approach_frame_audit.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 23
            mock_dt.now.side_effect = lambda *a, **kw: (
                real_dt(2026, 4, 29, 23, 0, tzinfo=real_tz.utc) if a else mock_now
            )
            ids = src.push(cortex)
        assert len(ids) >= 1
        cortex.store.assert_called_once()
        cortex.twm_push.assert_called_once()

    def test_skips_memories_below_threshold(self):
        src = ApproachFrameAuditSource()
        src._last_audit_ts = None
        src._last_check_ts = 0
        memories = {
            MemoryType.PROCEDURAL: [
                self._make_memory(
                    "mem-clean",
                    "Always stage files specifically by name. " + "word " * 200,
                ),
            ],
            MemoryType.CORE_PATTERN: [],
        }
        cortex = self._make_cortex(memories)
        from datetime import datetime as real_dt, timezone as real_tz

        with patch("wild_igor.igor.cognition.approach_frame_audit.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 23
            mock_dt.now.side_effect = lambda *a, **kw: (
                real_dt(2026, 4, 29, 23, 0, tzinfo=real_tz.utc) if a else mock_now
            )
            src.push(cortex)
        cortex.store.assert_not_called()
        cortex.twm_push.assert_not_called()

    def test_skips_already_pending_reframes(self):
        src = ApproachFrameAuditSource()
        src._last_audit_ts = None
        src._last_check_ts = 0
        memories = {
            MemoryType.PROCEDURAL: [
                self._make_memory(
                    "mem-prior",
                    "do not do this; never that; do not the other",
                    metadata={"pending_approach_reframe": True},
                ),
                self._make_memory(
                    "mem-prior2",
                    "do not do this; never that",
                    metadata={"approach_frame_audit_source": True},
                ),
            ],
            MemoryType.CORE_PATTERN: [],
        }
        cortex = self._make_cortex(memories)
        from datetime import datetime as real_dt, timezone as real_tz

        with patch("wild_igor.igor.cognition.approach_frame_audit.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 23
            mock_dt.now.side_effect = lambda *a, **kw: (
                real_dt(2026, 4, 29, 23, 0, tzinfo=real_tz.utc) if a else mock_now
            )
            src.push(cortex)
        cortex.store.assert_not_called()
        cortex.twm_push.assert_not_called()

    def test_top_n_cap(self):
        src = ApproachFrameAuditSource()
        src._last_audit_ts = None
        src._last_check_ts = 0
        # Make 15 memories all over threshold; top_n default is 10
        memories = {
            MemoryType.PROCEDURAL: [
                self._make_memory(
                    f"mem-{i}",
                    "do not do this; never bypass; must not skip; cannot avoid",
                )
                for i in range(15)
            ],
            MemoryType.CORE_PATTERN: [],
        }
        cortex = self._make_cortex(memories)
        from datetime import datetime as real_dt, timezone as real_tz

        with patch("wild_igor.igor.cognition.approach_frame_audit.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 23
            mock_dt.now.side_effect = lambda *a, **kw: (
                real_dt(2026, 4, 29, 23, 0, tzinfo=real_tz.utc) if a else mock_now
            )
            src.push(cortex)
        assert cortex.store.call_count == 10

    def test_last_audit_age_none_when_never_run(self):
        src = ApproachFrameAuditSource()
        assert src.last_audit_age_hours() is None

    def test_last_audit_age_after_run(self):
        src = ApproachFrameAuditSource()
        src._last_audit_ts = time.monotonic() - 7200
        age = src.last_audit_age_hours()
        assert age is not None
        assert 1.9 < age < 2.1

    def test_timing_tier_is_slow(self):
        assert ApproachFrameAuditSource.TIMING_TIER == "slow"

    def test_registered_in_push_sources(self):
        from wild_igor.igor.cognition import push_sources

        assert hasattr(push_sources, "approach_frame_audit_source")

"""
test_sleep_clock.py — T-sleep-triggered-by-clock (#467)

Tests for the clock-gated sleep safety net.
"""

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.sleep_clock import (  # noqa: E402
    MIN_AWAKE_HOURS,
    MIN_SLEEP_INTERVAL_SEC,
    SLEEP_WINDOW_END,
    SLEEP_WINDOW_START,
    SleepClockSource,
    _in_sleep_window,
)


class TestSleepWindow:
    def test_22_is_sleep(self):
        assert _in_sleep_window(22) is True

    def test_23_is_sleep(self):
        assert _in_sleep_window(23) is True

    def test_0_is_sleep(self):
        assert _in_sleep_window(0) is True

    def test_3_is_sleep(self):
        assert _in_sleep_window(3) is True

    def test_6_is_sleep(self):
        assert _in_sleep_window(6) is True

    def test_7_is_not_sleep(self):
        assert _in_sleep_window(7) is False

    def test_12_is_not_sleep(self):
        assert _in_sleep_window(12) is False

    def test_21_is_not_sleep(self):
        assert _in_sleep_window(21) is False


class TestSleepClockSource:
    def _make_cortex(self):
        cortex = MagicMock()
        cortex.twm_push.return_value = 1
        cortex.write_ring.return_value = None
        return cortex

    def test_fires_during_sleep_window(self):
        src = SleepClockSource()
        cortex = self._make_cortex()
        with patch("wild_igor.igor.cognition.sleep_clock.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=23)
            mock_dt.now.return_value.hour = 23
            mock_dt.side_effect = lambda *a, **k: MagicMock(
                isoformat=lambda: "2026-04-16T23:00:00Z"
            )
            from datetime import datetime as real_dt, timezone as real_tz

            with patch("wild_igor.igor.cognition.sleep_clock.datetime") as mock_dt2:
                mock_now = MagicMock()
                mock_now.hour = 23
                mock_dt2.now.side_effect = lambda *a, **kw: (
                    real_dt(2026, 4, 16, 23, 0, tzinfo=real_tz.utc) if a else mock_now
                )
                ids = src.push(cortex)
                assert len(ids) >= 1
                cortex.twm_push.assert_called_once()

    def test_does_not_fire_outside_sleep_window(self):
        src = SleepClockSource()
        cortex = self._make_cortex()
        with patch("wild_igor.igor.cognition.sleep_clock.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 14
            mock_dt.now.return_value = mock_now
            ids = src.push(cortex)
            assert ids == []

    def test_does_not_fire_if_recently_slept(self):
        src = SleepClockSource()
        src._last_sleep_ts = time.monotonic() - 60
        cortex = self._make_cortex()
        with patch("wild_igor.igor.cognition.sleep_clock.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 23
            mock_dt.now.return_value = mock_now
            ids = src.push(cortex)
            assert ids == []

    def test_fires_after_min_awake_hours(self):
        src = SleepClockSource()
        src._last_sleep_ts = time.monotonic() - (MIN_AWAKE_HOURS * 3600 + 100)
        src._last_check_ts = 0
        cortex = self._make_cortex()
        from datetime import datetime as real_dt, timezone as real_tz

        with patch("wild_igor.igor.cognition.sleep_clock.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 23
            mock_dt.now.side_effect = lambda *a, **kw: (
                real_dt(2026, 4, 16, 23, 0, tzinfo=real_tz.utc) if a else mock_now
            )
            ids = src.push(cortex)
            assert len(ids) >= 1

    def test_disabled_by_env_var(self):
        src = SleepClockSource()
        cortex = self._make_cortex()
        with patch.dict(os.environ, {"IGOR_SLEEP_CLOCK": "false"}):
            with patch("wild_igor.igor.cognition.sleep_clock.datetime") as mock_dt:
                mock_now = MagicMock()
                mock_now.hour = 23
                mock_dt.now.return_value = mock_now
                ids = src.push(cortex)
                assert ids == []

    def test_last_sleep_age_none_when_never_slept(self):
        src = SleepClockSource()
        assert src.last_sleep_age_hours() is None

    def test_last_sleep_age_after_sleep(self):
        src = SleepClockSource()
        src._last_sleep_ts = time.monotonic() - 7200
        age = src.last_sleep_age_hours()
        assert age is not None
        assert 1.9 < age < 2.1

    def test_timing_tier_is_slow(self):
        assert SleepClockSource.TIMING_TIER == "slow"

    def test_registered_in_push_sources(self):
        from wild_igor.igor.cognition import push_sources

        assert hasattr(push_sources, "sleep_clock_source")

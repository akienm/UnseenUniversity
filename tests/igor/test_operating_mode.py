"""
test_operating_mode.py — T-igor-modes

Tests for the four biological operating modes.
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.operating_mode import (
    Mode,
    derive_mode,
    mode_config,
)


class TestDeriveMode:
    def test_recent_input_is_foreground(self):
        now = time.monotonic()
        assert derive_mode(last_input_ts=now) == Mode.FOREGROUND

    def test_slightly_old_input_still_foreground(self):
        now = time.monotonic()
        assert derive_mode(last_input_ts=now - 60) == Mode.FOREGROUND  # 1 min ago

    def test_old_input_is_default(self):
        now = time.monotonic()
        # 10 min idle, not in sleep window
        with patch(
            "devices.igor.cognition.operating_mode._in_sleep_window",
            return_value=False,
        ):
            result = derive_mode(last_input_ts=now - 600)
        assert result == Mode.DEFAULT

    def test_very_old_input_is_consolidation(self):
        now = time.monotonic()
        # 30 min idle → sleep
        result = derive_mode(last_input_ts=now - 1800)
        assert result in (Mode.CONSOLIDATION, Mode.DREAMING)

    def test_sleep_window_triggers_consolidation(self):
        now = time.monotonic()
        with patch(
            "devices.igor.cognition.operating_mode._in_sleep_window",
            return_value=True,
        ):
            result = derive_mode(last_input_ts=now - 600)
        assert result == Mode.CONSOLIDATION

    def test_no_input_ever(self):
        result = derive_mode(last_input_ts=0)
        assert result in (Mode.CONSOLIDATION, Mode.DREAMING)

    def test_dreaming_phase_in_sleep(self):
        now = time.monotonic()
        # Sleep started long enough ago to hit dreaming phase
        # Dream cycle is 600s, dreaming starts at 1.5 * 600 = 900 into the 1200s cycle
        with patch(
            "devices.igor.cognition.operating_mode._in_sleep_window",
            return_value=True,
        ):
            result = derive_mode(
                last_input_ts=now - 1800,
                sleep_start_ts=now - 950,  # 950s into sleep = in dreaming phase
            )
        assert result == Mode.DREAMING


class TestModeConfig:
    def test_foreground_allows_cloud(self):
        cfg = mode_config(Mode.FOREGROUND)
        assert cfg["cloud_allowed"] is True
        assert cfg["response_expected"] is True

    def test_default_no_cloud(self):
        cfg = mode_config(Mode.DEFAULT)
        assert cfg["cloud_allowed"] is False
        assert cfg["response_expected"] is False

    def test_consolidation_is_maintenance(self):
        cfg = mode_config(Mode.CONSOLIDATION)
        assert cfg["push_source_tier"] == "maintenance"
        assert cfg["twm_behavior"] == "integrate"

    def test_dreaming_is_creative(self):
        cfg = mode_config(Mode.DREAMING)
        assert cfg["push_source_tier"] == "creative"
        assert cfg["twm_behavior"] == "random"

    def test_all_modes_have_config(self):
        for mode in Mode:
            cfg = mode_config(mode)
            assert "cloud_allowed" in cfg
            assert "twm_behavior" in cfg
            assert "response_expected" in cfg
            assert "push_source_tier" in cfg


class TestModeEnum:
    def test_four_modes(self):
        assert len(Mode) == 4

    def test_values(self):
        assert Mode.FOREGROUND.value == "foreground"
        assert Mode.DEFAULT.value == "default"
        assert Mode.CONSOLIDATION.value == "consolidation"
        assert Mode.DREAMING.value == "dreaming"

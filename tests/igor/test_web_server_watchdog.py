"""
test_web_server_watchdog.py — Tests for web_server_watchdog.py
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.web_server_watchdog import (  # noqa: E402
    DOWN_THRESHOLD,
    RESTART_COOLDOWN_SEC,
    WebServerWatchdog,
    is_web_server_up,
    relaunch_web_server,
)


class TestIsWebServerUp:
    def test_up_when_health_check_passes(self):
        with patch(
            "devices.igor.cognition.web_server_watchdog.is_web_server_up",
            return_value=True,
        ):
            # Calling the patched version directly just verifies patch works
            assert (
                is_web_server_up() is True
            )  # unpatched — will call actual _check_health

    def test_up_delegates_to_check_health(self):
        with patch(
            "devices.web_server.device._check_health", return_value={"status": "ok"}
        ):
            assert is_web_server_up() is True

    def test_down_when_check_health_returns_none(self):
        with patch("devices.web_server.device._check_health", return_value=None):
            assert is_web_server_up() is False

    def test_down_when_check_health_raises(self):
        with patch(
            "devices.web_server.device._check_health",
            side_effect=ImportError("no module"),
        ):
            assert is_web_server_up() is False


class TestRelaunchWebServer:
    def test_launches_via_device(self):
        mock_dev = MagicMock()
        with (
            patch(
                "devices.web_server.device.WebServerDevice",
                return_value=mock_dev,
            ),
            patch(
                "devices.igor.cognition.web_server_watchdog.is_web_server_up",
                return_value=True,
            ),
        ):
            ok = relaunch_web_server()
        assert ok is True
        mock_dev.start.assert_called_once()

    def test_returns_false_when_health_check_fails_after_start(self):
        mock_dev = MagicMock()
        with (
            patch(
                "devices.web_server.device.WebServerDevice",
                return_value=mock_dev,
            ),
            patch(
                "devices.igor.cognition.web_server_watchdog.is_web_server_up",
                return_value=False,
            ),
        ):
            ok = relaunch_web_server()
        assert ok is False

    def test_logs_error_on_exception(self):
        with (
            patch(
                "devices.web_server.device.WebServerDevice",
                side_effect=RuntimeError("boom"),
            ),
            patch("devices.igor.cognition.web_server_watchdog.log_error") as mock_log,
        ):
            ok = relaunch_web_server()
        assert ok is False
        mock_log.assert_called_once()


class TestWatchdogPush:
    def _cortex(self):
        c = MagicMock()
        c.twm_push.return_value = 1
        return c

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("IGOR_WEB_SERVER_WATCHDOG", "false")
        wd = WebServerWatchdog()
        assert wd.push(self._cortex()) == []

    def test_disabled_by_legacy_env(self, monkeypatch):
        monkeypatch.setenv("IGOR_UC_WATCHDOG", "false")
        wd = WebServerWatchdog()
        assert wd.push(self._cortex()) == []

    def test_resets_counter_when_up(self):
        wd = WebServerWatchdog()
        wd._consecutive_down = 5
        with patch(
            "devices.igor.cognition.web_server_watchdog.is_web_server_up",
            return_value=True,
        ):
            ids = wd.push(self._cortex())
        assert ids == []
        assert wd._consecutive_down == 0

    def test_no_relaunch_below_threshold(self):
        wd = WebServerWatchdog()
        with (
            patch(
                "devices.igor.cognition.web_server_watchdog.is_web_server_up",
                return_value=False,
            ),
            patch(
                "devices.igor.cognition.web_server_watchdog.relaunch_web_server"
            ) as mock_rl,
        ):
            wd.push(self._cortex())
        assert wd._consecutive_down == 1
        assert not mock_rl.called

    def test_relaunch_after_threshold(self):
        wd = WebServerWatchdog()
        wd._consecutive_down = DOWN_THRESHOLD - 1
        cortex = self._cortex()
        with (
            patch(
                "devices.igor.cognition.web_server_watchdog.is_web_server_up",
                return_value=False,
            ),
            patch(
                "devices.igor.cognition.web_server_watchdog.relaunch_web_server",
                return_value=True,
            ) as mock_rl,
        ):
            ids = wd.push(cortex)
        assert mock_rl.called
        assert len(ids) == 1
        cortex.twm_push.assert_called_once()
        assert cortex.twm_push.call_args.kwargs.get("category") == "watchdog"

    def test_cooldown_blocks_rapid_relaunch(self):
        wd = WebServerWatchdog()
        wd._consecutive_down = DOWN_THRESHOLD
        wd._last_restart_ts = time.monotonic() - 30  # 30s ago, well under cooldown
        with (
            patch(
                "devices.igor.cognition.web_server_watchdog.is_web_server_up",
                return_value=False,
            ),
            patch(
                "devices.igor.cognition.web_server_watchdog.relaunch_web_server"
            ) as mock_rl,
        ):
            wd.push(self._cortex())
        assert not mock_rl.called

    def test_cooldown_clears_after_window(self):
        wd = WebServerWatchdog()
        wd._consecutive_down = DOWN_THRESHOLD
        wd._last_restart_ts = time.monotonic() - (RESTART_COOLDOWN_SEC + 30)
        with (
            patch(
                "devices.igor.cognition.web_server_watchdog.is_web_server_up",
                return_value=False,
            ),
            patch(
                "devices.igor.cognition.web_server_watchdog.relaunch_web_server",
                return_value=True,
            ) as mock_rl,
        ):
            wd.push(self._cortex())
        assert mock_rl.called

    def test_timing_tier_slow(self):
        assert WebServerWatchdog.TIMING_TIER == "slow"

    def test_registered_in_push_sources(self):
        from devices.igor.cognition import push_sources

        assert hasattr(push_sources, "web_server_watchdog")

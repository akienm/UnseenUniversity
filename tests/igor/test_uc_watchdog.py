"""
test_uc_watchdog.py — T-uc-server-watchdog
"""

import os
import socket
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.uc_watchdog import (  # noqa: E402
    DOWN_THRESHOLD,
    RESTART_COOLDOWN_SEC,
    UtilityClosetWatchdog,
    is_uc_up,
    relaunch_uc,
)


class TestIsUcUp:
    def test_up_when_socket_connects(self):
        with patch(
            "devices.igor.cognition.uc_watchdog.socket.create_connection"
        ) as mock:
            mock.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock.return_value.__exit__ = MagicMock(return_value=False)
            assert is_uc_up() is True

    def test_down_when_oserror(self):
        with patch(
            "devices.igor.cognition.uc_watchdog.socket.create_connection",
            side_effect=OSError("connection refused"),
        ):
            assert is_uc_up() is False

    def test_down_when_timeout(self):
        with patch(
            "devices.igor.cognition.uc_watchdog.socket.create_connection",
            side_effect=socket.timeout("timed out"),
        ):
            assert is_uc_up() is False


class TestRelaunchUc:
    def test_skip_when_server_missing(self, tmp_path):
        with patch("devices.igor.cognition.uc_watchdog.log_error") as mock_log, patch(
            "devices.igor.cognition.uc_watchdog.subprocess.Popen"
        ) as mock_popen:
            ok = relaunch_uc(
                server_path=tmp_path / "missing.py",
                python_path=tmp_path / "py",
                log_path=tmp_path / "log",
            )
        assert ok is False
        assert not mock_popen.called
        mock_log.assert_called_once()

    def test_skip_when_python_missing(self, tmp_path):
        server = tmp_path / "uc.py"
        server.write_text("# stub")
        with patch("devices.igor.cognition.uc_watchdog.log_error") as mock_log, patch(
            "devices.igor.cognition.uc_watchdog.subprocess.Popen"
        ) as mock_popen:
            ok = relaunch_uc(
                server_path=server,
                python_path=tmp_path / "missing-python",
                log_path=tmp_path / "log",
            )
        assert ok is False
        assert not mock_popen.called

    def test_launches_with_setsid(self, tmp_path):
        server = tmp_path / "uc.py"
        server.write_text("# stub")
        python = tmp_path / "py"
        python.write_text("#!/bin/sh")
        log = tmp_path / "uc.log"
        with patch(
            "devices.igor.cognition.uc_watchdog.subprocess.Popen"
        ) as mock_popen:
            ok = relaunch_uc(server_path=server, python_path=python, log_path=log)
        assert ok is True
        kwargs = mock_popen.call_args.kwargs
        assert kwargs.get("start_new_session") is True
        assert kwargs.get("close_fds") is True

    def test_logs_error_on_oserror(self, tmp_path):
        server = tmp_path / "uc.py"
        server.write_text("# stub")
        python = tmp_path / "py"
        python.write_text("#!/bin/sh")
        with patch(
            "devices.igor.cognition.uc_watchdog.subprocess.Popen",
            side_effect=OSError("boom"),
        ), patch("devices.igor.cognition.uc_watchdog.log_error") as mock_log:
            ok = relaunch_uc(
                server_path=server,
                python_path=python,
                log_path=tmp_path / "uc.log",
            )
        assert ok is False
        mock_log.assert_called_once()


class TestWatchdogPush:
    def _cortex(self):
        c = MagicMock()
        c.twm_push.return_value = 1
        return c

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("IGOR_UC_WATCHDOG", "false")
        wd = UtilityClosetWatchdog()
        assert wd.push(self._cortex()) == []

    def test_resets_counter_when_up(self):
        wd = UtilityClosetWatchdog()
        wd._consecutive_down = 5
        with patch("devices.igor.cognition.uc_watchdog.is_uc_up", return_value=True):
            ids = wd.push(self._cortex())
        assert ids == []
        assert wd._consecutive_down == 0

    def test_no_relaunch_below_threshold(self):
        wd = UtilityClosetWatchdog()
        with patch(
            "devices.igor.cognition.uc_watchdog.is_uc_up", return_value=False
        ), patch("devices.igor.cognition.uc_watchdog.relaunch_uc") as mock_rl:
            wd.push(self._cortex())
        assert wd._consecutive_down == 1
        assert not mock_rl.called

    def test_relaunch_after_threshold(self):
        wd = UtilityClosetWatchdog()
        wd._consecutive_down = DOWN_THRESHOLD - 1
        cortex = self._cortex()
        with patch(
            "devices.igor.cognition.uc_watchdog.is_uc_up", return_value=False
        ), patch(
            "devices.igor.cognition.uc_watchdog.relaunch_uc", return_value=True
        ) as mock_rl:
            ids = wd.push(cortex)
        assert mock_rl.called
        assert len(ids) == 1
        cortex.twm_push.assert_called_once()
        # Watchdog category surfaced
        assert cortex.twm_push.call_args.kwargs.get("category") == "watchdog"

    def test_cooldown_blocks_rapid_relaunch(self):
        wd = UtilityClosetWatchdog()
        wd._consecutive_down = DOWN_THRESHOLD
        wd._last_restart_ts = time.monotonic() - 30  # 30s ago, well under cooldown
        with patch(
            "devices.igor.cognition.uc_watchdog.is_uc_up", return_value=False
        ), patch("devices.igor.cognition.uc_watchdog.relaunch_uc") as mock_rl:
            wd.push(self._cortex())
        assert not mock_rl.called

    def test_cooldown_clears_after_window(self):
        wd = UtilityClosetWatchdog()
        wd._consecutive_down = DOWN_THRESHOLD
        wd._last_restart_ts = time.monotonic() - (RESTART_COOLDOWN_SEC + 30)
        with patch(
            "devices.igor.cognition.uc_watchdog.is_uc_up", return_value=False
        ), patch(
            "devices.igor.cognition.uc_watchdog.relaunch_uc", return_value=True
        ) as mock_rl:
            wd.push(self._cortex())
        assert mock_rl.called

    def test_timing_tier_slow(self):
        assert UtilityClosetWatchdog.TIMING_TIER == "slow"

    def test_registered_in_push_sources(self):
        from devices.igor.cognition import push_sources

        assert hasattr(push_sources, "uc_watchdog")

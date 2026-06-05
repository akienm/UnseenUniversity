"""Tests for GrannyShim self-heal watchdog — dead daemon + pending tickets → restart."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch


class TestSelfTest:
    def test_no_pid_file_returns_not_passed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devices.granny.shim._GRANNY_HOME", tmp_path)
        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        result = shim.self_test()
        assert result["passed"] is False
        assert "no pid file" in result["details"]

    def test_live_pid_returns_passed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devices.granny.shim._GRANNY_HOME", tmp_path)
        import os

        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(str(os.getpid()))  # current process — definitely alive

        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        result = shim.self_test()
        assert result["passed"] is True

    def test_stale_pid_returns_not_passed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devices.granny.shim._GRANNY_HOME", tmp_path)
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text("999999999")  # PID that will never exist

        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        result = shim.self_test()
        assert result["passed"] is False
        assert "stale" in result["details"]


class TestWatchdogSelfHeal:
    def test_dead_daemon_with_tickets_triggers_restart(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devices.granny.shim._GRANNY_HOME", tmp_path)
        # Stale PID file
        (tmp_path / "daemon.pid").write_text("999999999")

        monkeypatch.setenv("GRANNY_SHIM_WATCHDOG_INTERVAL", "0")
        monkeypatch.setattr("devices.granny.shim._WATCHDOG_INTERVAL_SEC", 0)

        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        restart_called = threading.Event()

        def _mock_restart():
            restart_called.set()

        shim._restart_daemon = _mock_restart
        shim._has_pending_tickets = lambda: True

        # Run one watchdog iteration directly (not via thread) for determinism
        shim._watchdog_loop_once()
        assert restart_called.is_set(), "expected _restart_daemon to be called"

    def test_dead_daemon_no_tickets_does_not_restart(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devices.granny.shim._GRANNY_HOME", tmp_path)
        (tmp_path / "daemon.pid").write_text("999999999")

        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        restart_called = []
        shim._restart_daemon = lambda: restart_called.append(True)
        shim._has_pending_tickets = lambda: False

        shim._watchdog_loop_once()
        assert not restart_called, "expected no restart with no pending tickets"

    def test_no_pid_file_does_not_restart(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devices.granny.shim._GRANNY_HOME", tmp_path)
        # No pid file at all — Granny was never started on this host

        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        restart_called = []
        shim._restart_daemon = lambda: restart_called.append(True)
        shim._has_pending_tickets = lambda: True

        shim._watchdog_loop_once()
        assert not restart_called, "expected no restart when Granny was never started"

    def test_live_daemon_does_not_restart(self, tmp_path, monkeypatch):
        import os

        monkeypatch.setattr("devices.granny.shim._GRANNY_HOME", tmp_path)
        (tmp_path / "daemon.pid").write_text(str(os.getpid()))  # live PID

        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        restart_called = []
        shim._restart_daemon = lambda: restart_called.append(True)
        shim._has_pending_tickets = lambda: True

        shim._watchdog_loop_once()
        assert not restart_called, "expected no restart when daemon is alive"


class TestRestartDaemon:
    def test_restart_kills_session_and_starts_fresh(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devices.granny.shim._GRANNY_HOME", tmp_path)
        monkeypatch.setattr("devices.granny.shim._UU_ROOT", tmp_path)

        venv_python = tmp_path / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()

        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)

        with patch("devices.granny.shim._session_exists", return_value=True), \
             patch("devices.granny.shim.subprocess.run", side_effect=mock_run):
            shim._restart_daemon()

        session_cmds = [c for c in calls if "tmux" in c[0]]
        assert any("kill-session" in c for c in session_cmds), "expected kill-session call"
        assert any("new-session" in c for c in session_cmds), "expected new-session call"
        assert shim._relaunch_count == 1

    def test_restart_increments_relaunch_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr("devices.granny.shim._GRANNY_HOME", tmp_path)
        monkeypatch.setattr("devices.granny.shim._UU_ROOT", tmp_path)

        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        with patch("devices.granny.shim._session_exists", return_value=False), \
             patch("devices.granny.shim.subprocess.run", return_value=MagicMock(returncode=0)):
            shim._restart_daemon()
            shim._restart_daemon()
        assert shim._relaunch_count == 2


class TestStartStopWatchdog:
    def test_start_launches_watchdog_thread(self):
        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        assert shim._watchdog_thread is None
        shim.start()
        assert shim._watchdog_thread is not None
        assert shim._watchdog_thread.is_alive()
        shim._watchdog_stop.set()  # clean up

    def test_start_idempotent(self):
        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        shim.start()
        thread1 = shim._watchdog_thread
        shim.start()  # second call — should reuse existing thread
        assert shim._watchdog_thread is thread1
        shim._watchdog_stop.set()

    def test_stop_sets_watchdog_stop_event(self):
        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        shim.start()
        assert not shim._watchdog_stop.is_set()
        with patch("devices.granny.shim.os.kill"), \
             patch("pathlib.Path.exists", return_value=False):
            shim.stop()
        assert shim._watchdog_stop.is_set()

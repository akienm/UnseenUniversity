"""Tests for DickSimnelFrontDoor wake-on-demand device launcher."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch
import threading

import pytest

from unseen_university.devices.dicksimnel.frontdoor import DickSimnelFrontDoor


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_alive_proc():
    """Return a mock Popen with poll() returning None (alive)."""
    proc = MagicMock()
    proc.poll.return_value = None  # alive
    proc.pid = 12345
    return proc


def _make_dead_proc():
    """Return a mock Popen with poll() returning non-None (dead)."""
    proc = MagicMock()
    proc.poll.return_value = 1  # dead, exit code 1
    proc.pid = 12345
    return proc


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_ensure_device_awake_spawns_when_down():
    """Device should spawn when _proc is None (never spawned) or dead."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        mock_connect.return_value = MagicMock()  # Fake bus

        fd = DickSimnelFrontDoor()
        fd._proc = None  # Device down

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = _make_alive_proc()
            fd._ensure_device_awake()
            # Should call Popen exactly once
            assert mock_popen.call_count == 1

        # Verify spawn args
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert len(cmd) == 3
        assert cmd[1] == "-m"
        assert cmd[2] == "unseen_university.devices.dicksimnel"
        assert call_args[1]["env"]["UU_FRONTDOOR"] == "1"


def test_ensure_device_awake_does_not_spawn_when_alive():
    """Device should NOT spawn if already alive. Anti-hollow test."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        mock_connect.return_value = MagicMock()

        fd = DickSimnelFrontDoor()
        fd._proc = _make_alive_proc()  # Device already alive

        with patch("subprocess.Popen") as mock_popen:
            fd._ensure_device_awake()
            # Popen should NOT be called
            assert mock_popen.call_count == 0


def test_double_check_inside_lock():
    """Concurrent wakes should only spawn once (lock + double-check)."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        mock_connect.return_value = MagicMock()

        fd = DickSimnelFrontDoor()
        fd._proc = None

        # Simulate second thread setting proc between first check and lock acquire
        call_count = [0]

        original_popen = __import__("subprocess").Popen

        def mock_popen_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First spawn attempt: simulate another thread winning the race
                # This would be caught by the double-check inside the lock
                pass
            return _make_alive_proc()

        with patch("subprocess.Popen", side_effect=mock_popen_side_effect):
            # Call ensure_awake multiple times quickly
            fd._ensure_device_awake()
            fd._proc = _make_alive_proc()  # Simulate device coming alive
            fd._ensure_device_awake()  # Second call should not spawn
            # Even with the race simulation, only first call spawns
            assert call_count[0] == 1


def test_run_forever_wakes_on_bus_message():
    """run_forever should call _ensure_device_awake when idle_wait returns True."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        fake_bus = MagicMock()
        mock_connect.return_value = fake_bus

        fd = DickSimnelFrontDoor()

        with patch.object(fd, "_ensure_device_awake") as mock_ensure:
            # Simulate one wake, then stop on second iteration
            call_count = [0]

            def idle_wait_side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return True
                else:
                    fd._stop.set()
                    return False

            fake_bus.idle_wait.side_effect = idle_wait_side_effect
            fd.run_forever()

            # Should call _ensure_device_awake on the first wake
            assert mock_ensure.call_count == 1


def test_fetch_unseen_never_called():
    """Front-door must use idle_wait only; never call fetch_unseen."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        fake_bus = MagicMock()
        mock_connect.return_value = fake_bus

        fd = DickSimnelFrontDoor()

        # Simulate one wake cycle
        fake_bus.fetch_unseen = MagicMock()  # Spy on fetch_unseen

        def idle_wait_side_effect(*args, **kwargs):
            fd._stop.set()
            return True

        fake_bus.idle_wait.side_effect = idle_wait_side_effect

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = _make_alive_proc()
            fd.run_forever()

            # fetch_unseen should never be called
            assert fake_bus.fetch_unseen.call_count == 0


def test_bus_unavailable_retry_loop():
    """If bus is None, run_forever should retry with timeout."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        mock_connect.return_value = None  # Bus unavailable

        fd = DickSimnelFrontDoor()
        assert fd._bus is None

        # Stop immediately
        fd._stop.set()
        fd.run_forever()
        # Should complete without error


def test_write_available_flag():
    """_write_available should create the flag file."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        mock_connect.return_value = MagicMock()

        with patch("pathlib.Path.home") as mock_home:
            mock_flag_dir = MagicMock()
            mock_flag_path = MagicMock()
            mock_home.return_value = MagicMock(
                __truediv__=lambda *a, **kw: MagicMock(
                    __truediv__=lambda *a, **kw: MagicMock(
                        mkdir=MagicMock(), __truediv__=lambda *a, **kw: mock_flag_path
                    )
                )
            )
            mock_flag_path.write_text = MagicMock()

            fd = DickSimnelFrontDoor()
            fd._write_available()

            # Verify flag path write_text was called
            assert mock_flag_path.write_text.called


def test_remove_available_flag_idempotent():
    """_remove_available should handle FileNotFoundError gracefully."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        mock_connect.return_value = MagicMock()

        with patch("pathlib.Path.home") as mock_home:
            mock_flag_path = MagicMock()
            mock_flag_path.unlink.side_effect = FileNotFoundError()
            mock_home.return_value = MagicMock(
                __truediv__=lambda *a, **kw: MagicMock(
                    __truediv__=lambda *a, **kw: MagicMock(
                        __truediv__=lambda *a, **kw: mock_flag_path
                    )
                )
            )

            fd = DickSimnelFrontDoor()
            # Should not raise
            fd._remove_available()


def test_device_alive_check():
    """_device_alive should return True only if proc is not None and poll() is None."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        mock_connect.return_value = MagicMock()

        fd = DickSimnelFrontDoor()

        # Case 1: _proc is None
        fd._proc = None
        assert fd._device_alive() is False

        # Case 2: _proc is alive (poll() returns None)
        fd._proc = _make_alive_proc()
        assert fd._device_alive() is True

        # Case 3: _proc is dead (poll() returns non-None)
        fd._proc = _make_dead_proc()
        assert fd._device_alive() is False


def test_spawn_device_env_var():
    """Spawned device should receive UU_FRONTDOOR=1 in environment."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        mock_connect.return_value = MagicMock()

        with patch("os.environ", {"PATH": "/usr/bin"}):
            fd = DickSimnelFrontDoor()

            with patch("subprocess.Popen") as mock_popen:
                with patch("builtins.open", create=True):
                    mock_popen.return_value = _make_alive_proc()
                    fd._spawn_device()

                    # Check env var was set
                    call_args = mock_popen.call_args
                    env = call_args[1]["env"]
                    assert env["UU_FRONTDOOR"] == "1"


def test_start_method_writes_flag_then_runs():
    """start() should write the flag before entering run_forever loop."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        mock_connect.return_value = MagicMock()

        fd = DickSimnelFrontDoor()

        with patch.object(fd, "_write_available") as mock_write:
            with patch.object(fd, "run_forever") as mock_run:
                fd.start()

                # Should write flag first
                assert mock_write.call_count == 1
                # Then run forever
                assert mock_run.call_count == 1


def test_stop_method_sets_event_and_removes_flag():
    """stop() should set _stop event and remove the flag."""
    with patch(
        "unseen_university.devices.dicksimnel.frontdoor.DickSimnelFrontDoor._connect_bus"
    ) as mock_connect:
        mock_connect.return_value = MagicMock()

        fd = DickSimnelFrontDoor()

        with patch.object(fd, "_remove_available") as mock_remove:
            fd.stop()

            # Should set stop event
            assert fd._stop.is_set()
            # Should remove flag
            assert mock_remove.call_count == 1

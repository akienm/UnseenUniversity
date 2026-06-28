"""
Tests for devices/ground_loop/plugin_daemon.py CC recovery.

Tests:
- _fire_cc_recovery: spawns CC with structured prompt
- _fire_cc_recovery: includes stderr tail in prompt when log exists
- _fire_cc_recovery: handles missing stderr log gracefully
- _fire_cc_recovery: handles CC spawn failure gracefully (never raises)
- PluginDaemon: calls _fire_cc_recovery after max_restarts
- PluginDaemon: stderr goes to log file, not DEVNULL
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.ground_loop.plugin_daemon import (
    PluginDaemon,
    _fire_cc_recovery,
)


# ── _fire_cc_recovery ─────────────────────────────────────────────────────────

def test_cc_recovery_spawns_claude(tmp_path):
    """_fire_cc_recovery spawns claude --dangerously-skip-permissions."""
    with patch("subprocess.Popen") as mock_popen:
        _fire_cc_recovery("test-plugin", "max_restarts exceeded")

    assert mock_popen.called
    cmd = mock_popen.call_args[0][0]
    assert cmd[0] == "claude"
    assert "--dangerously-skip-permissions" in cmd
    assert "-p" in cmd
    # The prompt arg follows -p
    prompt_idx = cmd.index("-p") + 1
    prompt = cmd[prompt_idx]
    assert "test-plugin" in prompt
    assert "max_restarts exceeded" in prompt


def test_cc_recovery_includes_stderr_tail(tmp_path):
    """When a stderr log exists, last N lines are included in the CC prompt."""
    log_file = tmp_path / "test-plugin.stderr.log"
    log_file.write_text("line1\nline2\nERROR: something broke\n")

    with patch("subprocess.Popen") as mock_popen:
        _fire_cc_recovery("test-plugin", "failure reason", stderr_log=log_file)

    prompt = mock_popen.call_args[0][0][mock_popen.call_args[0][0].index("-p") + 1]
    assert "something broke" in prompt


def test_cc_recovery_missing_stderr_log(tmp_path):
    """Missing stderr log → no_stderr captured message, no exception."""
    log_file = tmp_path / "nonexistent.stderr.log"

    with patch("subprocess.Popen") as mock_popen:
        _fire_cc_recovery("test-plugin", "reason", stderr_log=log_file)

    prompt = mock_popen.call_args[0][0][mock_popen.call_args[0][0].index("-p") + 1]
    assert "no stderr captured" in prompt


def test_cc_recovery_spawn_failure_does_not_raise():
    """If CC spawn fails, _fire_cc_recovery catches the exception and returns."""
    with patch("subprocess.Popen", side_effect=FileNotFoundError("claude not found")):
        # Must not raise
        _fire_cc_recovery("test-plugin", "reason")


# ── PluginDaemon integration ──────────────────────────────────────────────────

def _make_daemon(tmp_path, max_restarts: int = 1) -> PluginDaemon:
    cfg = {
        "name": "test-svc",
        "start_cmd": ["/bin/false"],
        "poll_interval": 5,
        "max_restarts": max_restarts,
        "on_failure": "cc_recovery",
    }
    d = PluginDaemon(cfg)
    # Override paths to tmp
    return d


def test_daemon_fires_recovery_after_max_restarts(tmp_path):
    """After max_restarts+1 deaths, PluginDaemon fires cc_recovery."""
    daemon = _make_daemon(tmp_path, max_restarts=1)

    mock_dead_proc = MagicMock()
    mock_dead_proc.poll.return_value = 1  # process is dead
    mock_dead_proc.returncode = 1
    mock_dead_proc.pid = 9999
    daemon._proc = mock_dead_proc
    daemon._restart_count = 2  # already exceeded max_restarts=1

    with patch("unseen_university.devices.ground_loop.plugin_daemon._fire_cc_recovery") as mock_recovery, \
         patch("unseen_university.devices.ground_loop.plugin_daemon._FLAGS_DIR", tmp_path):
        daemon.tick()

    mock_recovery.assert_called_once()
    assert mock_recovery.call_args[0][0] == "test-svc"


def test_daemon_stderr_goes_to_log_file(tmp_path):
    """PluginDaemon spawns process with stderr → per-plugin log file, not DEVNULL."""
    daemon = _make_daemon(tmp_path)

    with patch("unseen_university.devices.ground_loop.plugin_daemon._FLAGS_DIR", tmp_path), \
         patch("unseen_university.devices.ground_loop.plugin_daemon._STDERR_DIR", tmp_path), \
         patch("subprocess.Popen") as mock_popen, \
         patch("builtins.open", return_value=MagicMock()) as mock_open:
        mock_popen.return_value = MagicMock(pid=1234)
        daemon._spawn()

    # open() should have been called for the stderr log (not DEVNULL)
    assert mock_open.called
    opened_path = str(mock_open.call_args[0][0])
    assert "test-svc" in opened_path
    assert "stderr.log" in opened_path

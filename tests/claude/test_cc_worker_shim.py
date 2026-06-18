"""
tests/claude/test_cc_worker_shim.py — CCWorkerShim unit tests.

Tests:
- device_id matches worker_id
- mailbox derived correctly from worker_id
- start() no-ops when circuit is OPEN
- start() launches listener when circuit is CLOSED
- stop() calls SIGTERM on known pid + marks unavailable
- self_test() reflects listener alive/dead
- ensure_daemon_running() restarts listener when circuit CLOSED + dead
- ensure_daemon_running() no-op when circuit OPEN
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from devices.claude.worker_shim import CCWorkerShim, _listener_pid_file, _worker_id_to_mailbox


# ── helpers ───────────────────────────────────────────────────────────────────


def _shim(worker_id: str = "CC.1", tmp_path: Path | None = None) -> CCWorkerShim:
    """Build a test shim. Uses tmp_path so nothing touches ~/.granny."""
    s = CCWorkerShim(worker_id, tmux_session="test-cc-session")
    if tmp_path:
        s._pid_file = tmp_path / f"cc_listener_{worker_id}.pid"
    return s


# ── unit ──────────────────────────────────────────────────────────────────────


def test_device_id():
    assert _shim("CC.1").device_id == "CC.1"
    assert _shim("CC.0").device_id == "CC.0"


def test_mailbox_derived():
    assert _shim("CC.1")._mailbox == "cc.1"
    assert _shim("CC.0")._mailbox == "cc.0"


def test_worker_name_defaults():
    assert _shim("CC.0")._worker_name == "claude"
    assert _shim("CC.1")._worker_name == "cc.1"


def test_listener_pid_file_per_slot():
    """CC.0 and CC.1 get distinct pid files."""
    f0 = _listener_pid_file("cc.0")
    f1 = _listener_pid_file("cc.1")
    assert f0 != f1


def test_worker_id_to_mailbox():
    assert _worker_id_to_mailbox("CC.0") == "cc.0"
    assert _worker_id_to_mailbox("CC.1") == "cc.1"


# ── circuit breaker ────────────────────────────────────────────────────────────


def test_start_no_ops_when_circuit_open(tmp_path):
    s = _shim(tmp_path=tmp_path)
    with patch.object(s, "check_circuit", return_value=True), \
         patch("subprocess.Popen") as mock_popen:
        result = s.start()
    assert result is False
    mock_popen.assert_not_called()


def test_start_launches_listener_when_circuit_closed(tmp_path):
    s = _shim(tmp_path=tmp_path)
    mock_proc = MagicMock()
    mock_proc.pid = 9999
    with patch.object(s, "check_circuit", return_value=False), \
         patch.object(s, "_listener_alive", return_value=False), \
         patch("subprocess.Popen", return_value=mock_proc), \
         patch("time.sleep"), \
         patch.object(s, "_mark_available"), \
         patch.object(s, "_post_status"):
        result = s.start()
    assert result is True


def test_start_no_ops_if_listener_already_alive(tmp_path):
    s = _shim(tmp_path=tmp_path)
    with patch.object(s, "check_circuit", return_value=False), \
         patch.object(s, "_listener_alive", return_value=True), \
         patch("subprocess.Popen") as mock_popen:
        result = s.start()
    assert result is True
    mock_popen.assert_not_called()


# ── stop ──────────────────────────────────────────────────────────────────────


def test_stop_sends_sigterm(tmp_path):
    s = _shim(tmp_path=tmp_path)
    pid_file = tmp_path / f"cc_listener_CC.1.pid"
    s._pid_file = pid_file
    pid_file.write_text("1234")

    with patch("os.kill") as mock_kill, \
         patch("devices.granny.announce_worker.withdraw"), \
         patch.object(s, "_mark_unavailable"), \
         patch.object(s, "_cancel_active_handshakes"), \
         patch.object(s, "_post_status"):
        s.stop()

    import signal as _signal
    mock_kill.assert_called_once_with(1234, _signal.SIGTERM)


def test_stop_graceful_when_no_pid(tmp_path):
    s = _shim(tmp_path=tmp_path)
    # No pid file → stop() should not raise
    with patch("devices.granny.announce_worker.withdraw"), \
         patch.object(s, "_mark_unavailable"), \
         patch.object(s, "_cancel_active_handshakes"), \
         patch.object(s, "_post_status"):
        result = s.stop()
    assert result is True


# ── self_test ─────────────────────────────────────────────────────────────────


def test_self_test_passes_when_listener_alive(tmp_path):
    s = _shim(tmp_path=tmp_path)
    with patch.object(s, "_listener_alive", return_value=True), \
         patch.object(s, "_tmux_session_exists", return_value=True):
        result = s.self_test()
    assert result["passed"] is True


def test_self_test_fails_when_listener_dead(tmp_path):
    s = _shim(tmp_path=tmp_path)
    with patch.object(s, "_listener_alive", return_value=False), \
         patch.object(s, "_tmux_session_exists", return_value=False):
        result = s.self_test()
    assert result["passed"] is False
    assert "dead" in result["details"]


# ── ensure_daemon_running ──────────────────────────────────────────────────────


def test_ensure_daemon_no_op_when_circuit_open(tmp_path):
    s = _shim(tmp_path=tmp_path)
    with patch.object(s, "check_circuit", return_value=True), \
         patch.object(s, "start") as mock_start:
        result = s.ensure_daemon_running()
    assert result is True
    mock_start.assert_not_called()


def test_ensure_daemon_no_op_when_listener_alive(tmp_path):
    s = _shim(tmp_path=tmp_path)
    with patch.object(s, "check_circuit", return_value=False), \
         patch.object(s, "_listener_alive", return_value=True), \
         patch.object(s, "start") as mock_start:
        result = s.ensure_daemon_running()
    assert result is True
    mock_start.assert_not_called()


def test_ensure_daemon_restarts_when_listener_dead(tmp_path):
    s = _shim(tmp_path=tmp_path)
    with patch.object(s, "check_circuit", return_value=False), \
         patch.object(s, "_listener_alive", return_value=False), \
         patch.object(s, "start", return_value=True) as mock_start:
        result = s.ensure_daemon_running()
    assert result is True
    mock_start.assert_called_once()

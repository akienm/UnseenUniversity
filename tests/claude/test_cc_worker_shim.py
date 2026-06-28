"""
tests/claude/test_cc_worker_shim.py — CCWorkerShim unit tests.

Tests:
- device_id matches worker_id
- mailbox derived correctly from worker_id
- worker_name defaults
- start() no-ops when circuit is OPEN
- start() creates CCWorkerListener thread when circuit is CLOSED
- start() no-ops when listener thread already alive
- stop() calls listener.stop() and clears reference
- stop() graceful when no listener instance
- self_test() reflects listener alive/dead via is_alive()
- ensure_daemon_running() restarts listener when circuit CLOSED + dead
- ensure_daemon_running() no-op when circuit OPEN + listener dead
- ensure_daemon_running() STOPS listener when circuit OPEN + listener alive (bidirectional)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from unseen_university.devices.claude.worker_shim import CCWorkerShim, _worker_id_to_mailbox


# ── helpers ───────────────────────────────────────────────────────────────────


def _shim(worker_id: str = "CC.1") -> CCWorkerShim:
    """Build a test shim."""
    return CCWorkerShim(worker_id, tmux_session="test-cc-session")


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


def test_worker_id_to_mailbox():
    assert _worker_id_to_mailbox("CC.0") == "cc.0"
    assert _worker_id_to_mailbox("CC.1") == "cc.1"


# ── circuit breaker ────────────────────────────────────────────────────────────


def test_start_no_ops_when_circuit_open():
    s = _shim()
    with patch.object(s, "check_circuit", return_value=True):
        with patch("unseen_university.devices.bus.connection.make_bus_connection") as mock_bus:
            result = s.start()
    assert result is False
    mock_bus.assert_not_called()


def test_start_creates_listener_thread_when_circuit_closed():
    s = _shim()
    mock_imap = MagicMock()
    mock_listener = MagicMock()
    mock_listener.is_alive.return_value = True

    with patch.object(s, "check_circuit", return_value=False), \
         patch.object(s, "_listener_alive", return_value=False), \
         patch("unseen_university.devices.claude.worker_shim.announce"), \
         patch.object(s, "_mark_available"), \
         patch.object(s, "_post_status"), \
         patch("unseen_university.devices.granny.cc_worker_listener.CCWorkerListener", return_value=mock_listener) as MockCWL, \
         patch("unseen_university.devices.bus.connection.make_bus_connection", return_value=mock_imap):
        result = s.start()

    assert result is True
    mock_listener.start.assert_called_once()


def test_start_no_ops_if_listener_already_alive():
    s = _shim()
    with patch.object(s, "check_circuit", return_value=False), \
         patch.object(s, "_listener_alive", return_value=True):
        result = s.start()
    assert result is True


# ── stop ──────────────────────────────────────────────────────────────────────


def test_stop_calls_listener_stop():
    s = _shim()
    mock_listener = MagicMock()
    s._listener = mock_listener

    with patch("unseen_university.devices.claude.worker_shim.withdraw"), \
         patch.object(s, "_mark_unavailable"), \
         patch.object(s, "_cancel_active_handshakes"), \
         patch.object(s, "_post_status"):
        s.stop()

    mock_listener.stop.assert_called_once()
    assert s._listener is None


def test_stop_clears_listener_reference():
    s = _shim()
    s._listener = MagicMock()

    with patch("unseen_university.devices.claude.worker_shim.withdraw"), \
         patch.object(s, "_mark_unavailable"), \
         patch.object(s, "_cancel_active_handshakes"), \
         patch.object(s, "_post_status"):
        s.stop()

    assert s._listener is None


def test_stop_graceful_when_no_listener():
    s = _shim()
    assert s._listener is None

    with patch("unseen_university.devices.claude.worker_shim.withdraw"), \
         patch.object(s, "_mark_unavailable"), \
         patch.object(s, "_cancel_active_handshakes"), \
         patch.object(s, "_post_status"):
        result = s.stop()

    assert result is True


# ── self_test ─────────────────────────────────────────────────────────────────


def test_self_test_passes_when_listener_alive():
    s = _shim()
    with patch.object(s, "_listener_alive", return_value=True), \
         patch.object(s, "_tmux_session_exists", return_value=True):
        result = s.self_test()
    assert result["passed"] is True


def test_self_test_fails_when_listener_dead():
    s = _shim()
    with patch.object(s, "_listener_alive", return_value=False), \
         patch.object(s, "_tmux_session_exists", return_value=False):
        result = s.self_test()
    assert result["passed"] is False
    assert "dead" in result["details"]


# ── ensure_daemon_running ──────────────────────────────────────────────────────


def test_ensure_daemon_no_op_when_circuit_open_listener_dead():
    s = _shim()
    with patch.object(s, "check_circuit", return_value=True), \
         patch.object(s, "_listener_alive", return_value=False), \
         patch.object(s, "start") as mock_start, \
         patch.object(s, "stop") as mock_stop:
        result = s.ensure_daemon_running()
    assert result is True
    mock_start.assert_not_called()
    mock_stop.assert_not_called()


def test_ensure_daemon_stops_when_circuit_open_listener_alive():
    """Bidirectional: OPEN + alive → stop()."""
    s = _shim()
    with patch.object(s, "check_circuit", return_value=True), \
         patch.object(s, "_listener_alive", return_value=True), \
         patch.object(s, "stop") as mock_stop:
        result = s.ensure_daemon_running()
    assert result is True
    mock_stop.assert_called_once()


def test_ensure_daemon_no_op_when_listener_alive():
    s = _shim()
    with patch.object(s, "check_circuit", return_value=False), \
         patch.object(s, "_listener_alive", return_value=True), \
         patch.object(s, "start") as mock_start:
        result = s.ensure_daemon_running()
    assert result is True
    mock_start.assert_not_called()


def test_ensure_daemon_restarts_when_listener_dead():
    s = _shim()
    with patch.object(s, "check_circuit", return_value=False), \
         patch.object(s, "_listener_alive", return_value=False), \
         patch.object(s, "start", return_value=True) as mock_start:
        result = s.ensure_daemon_running()
    assert result is True
    mock_start.assert_called_once()

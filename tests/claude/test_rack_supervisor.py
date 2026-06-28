"""
tests/claude/test_rack_supervisor.py — RackSupervisor unit tests.

Tests:
- All configured slots get a CCWorkerShim instance on start()
- Ground Loop YAML descriptor is written on start()
- _tick() calls ensure_daemon_running() on each shim
- _tick() isolates errors per slot (one failure doesn't stop the others)
- _shutdown() calls stop() on all shims
- stop() sets the threading.Event (preempts run_forever)
- run_forever() exits when stop() is called
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


def _make_supervisor(tmp_path: Path, slots=None):
    from unseen_university.devices.claude.rack_supervisor import RackSupervisor

    return RackSupervisor(
        slots=slots if slots is not None else ["CC.0", "CC.1"],
        poll_interval=30,
        plugin_dir=tmp_path / "ground_loop",
    )


# ── slot registration ──────────────────────────────────────────────────────────


def test_all_slots_get_a_shim(tmp_path):
    sup = _make_supervisor(tmp_path)
    with patch("unseen_university.devices.claude.rack_supervisor.CCWorkerShim") as MockShim:
        MockShim.side_effect = lambda slot_id: MagicMock(device_id=slot_id)
        sup.start()
    assert set(sup._shims.keys()) == {"CC.0", "CC.1"}


def test_custom_slots_respected(tmp_path):
    sup = _make_supervisor(tmp_path, slots=["CC.1"])
    with patch("unseen_university.devices.claude.rack_supervisor.CCWorkerShim", return_value=MagicMock()):
        sup.start()
    assert list(sup._shims.keys()) == ["CC.1"]


def test_empty_slot_list(tmp_path):
    sup = _make_supervisor(tmp_path, slots=[])
    with patch("unseen_university.devices.claude.rack_supervisor.CCWorkerShim") as MockShim:
        sup.start()
    MockShim.assert_not_called()
    assert sup._shims == {}


# ── Ground Loop descriptor ─────────────────────────────────────────────────────


def test_descriptor_written_on_start(tmp_path):
    sup = _make_supervisor(tmp_path, slots=[])
    with patch("unseen_university.devices.claude.rack_supervisor.CCWorkerShim"):
        sup.start()
    dest = tmp_path / "ground_loop" / "rack_supervisor.yaml"
    assert dest.exists(), "Ground Loop descriptor must be written on start()"
    cfg = yaml.safe_load(dest.read_text())
    assert cfg["name"] == "rack_supervisor"
    assert cfg["mode"] == "daemon"
    assert isinstance(cfg["start_cmd"], list)
    assert len(cfg["start_cmd"]) >= 2


def test_descriptor_is_valid_yaml(tmp_path):
    sup = _make_supervisor(tmp_path, slots=[])
    with patch("unseen_university.devices.claude.rack_supervisor.CCWorkerShim"):
        sup.start()
    dest = tmp_path / "ground_loop" / "rack_supervisor.yaml"
    cfg = yaml.safe_load(dest.read_text())
    assert "poll_interval" in cfg
    assert "max_restarts" in cfg


# ── watchdog tick ──────────────────────────────────────────────────────────────


def test_tick_calls_ensure_daemon_running_per_slot(tmp_path):
    sup = _make_supervisor(tmp_path)
    mock_cc0 = MagicMock()
    mock_cc1 = MagicMock()
    sup._shims = {"CC.0": mock_cc0, "CC.1": mock_cc1}
    sup._tick()
    mock_cc0.ensure_daemon_running.assert_called_once()
    mock_cc1.ensure_daemon_running.assert_called_once()


def test_tick_isolates_errors_per_slot(tmp_path):
    sup = _make_supervisor(tmp_path)
    mock_bad = MagicMock()
    mock_bad.ensure_daemon_running.side_effect = RuntimeError("boom")
    mock_ok = MagicMock()
    sup._shims = {"CC.bad": mock_bad, "CC.0": mock_ok}
    sup._tick()  # must not raise
    mock_ok.ensure_daemon_running.assert_called_once()


# ── shutdown ────────────────────────────────────────────────────────────────────


def test_shutdown_stops_all_slots(tmp_path):
    sup = _make_supervisor(tmp_path)
    mock_cc0 = MagicMock()
    mock_cc1 = MagicMock()
    sup._shims = {"CC.0": mock_cc0, "CC.1": mock_cc1}
    sup._shutdown()
    mock_cc0.stop.assert_called_once()
    mock_cc1.stop.assert_called_once()


def test_shutdown_continues_after_stop_error(tmp_path):
    sup = _make_supervisor(tmp_path)
    mock_bad = MagicMock()
    mock_bad.stop.side_effect = RuntimeError("stop failed")
    mock_ok = MagicMock()
    sup._shims = {"CC.bad": mock_bad, "CC.ok": mock_ok}
    sup._shutdown()  # must not raise
    mock_ok.stop.assert_called_once()


# ── stop + run_forever ────────────────────────────────────────────────────────


def test_stop_sets_threading_event(tmp_path):
    sup = _make_supervisor(tmp_path)
    assert not sup._stop.is_set()
    sup.stop()
    assert sup._stop.is_set()


def test_run_forever_exits_on_stop(tmp_path):
    """run_forever must return when stop() preempts the wait."""
    sup = _make_supervisor(tmp_path, slots=[])
    sup._shims = {}
    sup._poll_interval = 60  # long poll — stop() must preempt

    # Fire stop() from a timer thread 50ms after run_forever starts
    t = threading.Timer(0.05, sup.stop)
    t.start()
    try:
        sup.run_forever()  # must return within ~100ms, not block 60s
    finally:
        t.cancel()

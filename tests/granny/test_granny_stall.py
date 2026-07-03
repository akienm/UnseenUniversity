"""Test granny stall detection and MRU worker ordering.

PROOF 1: test_stalled_granny_parks_no_respin — Granny raises alarm once, parks,
and early-returns on subsequent cycles (no respin).

PROOF 2: test_mru_orders_most_recent_first — MRU list correctly ranks workers
by dispatch recency.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


def test_stalled_granny_parks_no_respin(tmp_path, monkeypatch):
    """PROOF: Granny stalls when work exists but no worker is available.

    First cycle: pending ticket + unavailable worker → alarm raised + stalled flag set.
    Second cycle: is_stalled() early-returns → alarm NOT raised again (dedup).
    """
    from unseen_university.devices.granny import daemon, stall_state

    # Monkeypatch FIRST (before any stall_state calls).
    monkeypatch.setattr(stall_state, "_STATE", tmp_path / "stall_state.json")
    monkeypatch.setattr(stall_state, "_MRU", tmp_path / "mru.json")

    # Ensure not stalled at start
    stall_state.resume()

    config = {
        "workers": {
            "DickSimnel.0": {
                "dispatch": "bus",
                "mailbox": "dicksimnel.0",
                "worker_name": "dicksimnel",
            }
        },
        "rules": [
            {"when": {"role_in": ["builder"]}, "route_to": "DickSimnel.0"},
            {"route_to": "CC.1"},
        ],
        "granny_mailbox": "granny.0",
    }

    def mock_sprint_tickets():
        return [
            {
                "id": "T-stall-test",
                "status": "sprint",
                "role": "builder",
                "title": "test ticket",
                "tags": [],
            }
        ]

    def mock_cleared_gated():
        return []

    def mock_handshake(*a, **k):
        return 0

    def mock_escalate(*a, **k):
        return 0

    def mock_reset_stale(*a, **k):
        return 0

    # No available worker
    def mock_is_available(wid, *a, **k):
        return False

    def mock_get_executor():
        executor = MagicMock()
        executor.tick = MagicMock()
        return executor

    # Spy on raise_alarm to count calls
    with patch.object(daemon, "_sprint_tickets", side_effect=mock_sprint_tickets), \
         patch.object(daemon, "_cleared_gated_tickets", side_effect=mock_cleared_gated), \
         patch.object(daemon, "_process_handshake_replies", side_effect=mock_handshake), \
         patch.object(daemon, "_escalate_stale_dispatched", side_effect=mock_escalate), \
         patch.object(daemon, "_reset_stale_inprogress", side_effect=mock_reset_stale), \
         patch("unseen_university.devices.granny.availability.is_available", side_effect=mock_is_available), \
         patch("unseen_university.devices.granny.availability.check_and_expire_cooldowns"), \
         patch("unseen_university.devices.granny.daemon.raise_alarm") as mock_alarm, \
         patch("unseen_university.devices.granny.workflow_executor.get_executor", side_effect=mock_get_executor):
        # First call: should raise alarm and set stalled
        daemon.run_once(config, imap=None)
        assert mock_alarm.call_count == 1
        assert stall_state.is_stalled() is True

        # Second call: is_stalled() early-returns, no alarm
        daemon.run_once(config, imap=None)
        assert mock_alarm.call_count == 1  # Not incremented
        assert stall_state.is_stalled() is True


def test_mru_orders_most_recent_first(tmp_path, monkeypatch):
    """PROOF: MRU list tracks dispatch order and mru_order ranks by recency."""
    from unseen_university.devices.granny import stall_state

    # Monkeypatch FIRST
    monkeypatch.setattr(stall_state, "_STATE", tmp_path / "stall_state.json")
    monkeypatch.setattr(stall_state, "_MRU", tmp_path / "mru.json")

    # Record dispatches in order: A, then B
    stall_state.record_dispatch("A")
    stall_state.record_dispatch("B")

    # B should rank first (most recent), A second
    result = stall_state.mru_order(["A", "B"])
    assert result == ["B", "A"]

    # Test with candidates not in MRU: should preserve original order after ranked
    result = stall_state.mru_order(["C", "B", "A"])
    assert result == ["B", "A", "C"]  # B and A ranked by MRU, C (unranked) last


def test_resume_clears_stalled(tmp_path, monkeypatch):
    """Non-proof: resume() clears the stall state."""
    from unseen_university.devices.granny import stall_state

    # Monkeypatch FIRST
    monkeypatch.setattr(stall_state, "_STATE", tmp_path / "stall_state.json")
    monkeypatch.setattr(stall_state, "_MRU", tmp_path / "mru.json")

    # Set stalled
    stall_state.set_stalled("T-test", "test reason")
    assert stall_state.is_stalled() is True

    # Resume
    stall_state.resume()
    assert stall_state.is_stalled() is False


def test_available_worker_does_not_stall(tmp_path, monkeypatch):
    """Non-proof: when a worker is available, no stall alarm is raised."""
    from unseen_university.devices.granny import daemon, stall_state

    # Monkeypatch FIRST
    monkeypatch.setattr(stall_state, "_STATE", tmp_path / "stall_state.json")
    monkeypatch.setattr(stall_state, "_MRU", tmp_path / "mru.json")

    stall_state.resume()

    config = {
        "workers": {
            "DickSimnel.0": {
                "dispatch": "bus",
                "mailbox": "dicksimnel.0",
                "worker_name": "dicksimnel",
            }
        },
        "rules": [
            {"when": {"role_in": ["builder"]}, "route_to": "DickSimnel.0"},
        ],
        "granny_mailbox": "granny.0",
    }

    def mock_sprint_tickets():
        return [
            {
                "id": "T-avail-test",
                "status": "sprint",
                "role": "builder",
                "title": "test ticket",
                "tags": [],
            }
        ]

    def mock_cleared_gated():
        return []

    def mock_handshake(*a, **k):
        return 0

    def mock_escalate(*a, **k):
        return 0

    def mock_reset_stale(*a, **k):
        return 0

    # Worker IS available
    def mock_is_available(wid, *a, **k):
        return True

    # Dispatch succeeds (no-op for this test)
    def mock_dispatch_bus(*a, **k):
        return True

    def mock_get_executor():
        executor = MagicMock()
        executor.tick = MagicMock()
        return executor

    with patch.object(daemon, "_sprint_tickets", side_effect=mock_sprint_tickets), \
         patch.object(daemon, "_cleared_gated_tickets", side_effect=mock_cleared_gated), \
         patch.object(daemon, "_process_handshake_replies", side_effect=mock_handshake), \
         patch.object(daemon, "_escalate_stale_dispatched", side_effect=mock_escalate), \
         patch.object(daemon, "_reset_stale_inprogress", side_effect=mock_reset_stale), \
         patch("unseen_university.devices.granny.availability.is_available", side_effect=mock_is_available), \
         patch("unseen_university.devices.granny.availability.check_and_expire_cooldowns"), \
         patch.object(daemon, "_dispatch_bus", side_effect=mock_dispatch_bus), \
         patch("unseen_university.devices.granny.daemon.raise_alarm") as mock_alarm, \
         patch("unseen_university.devices.granny.workflow_executor.get_executor", side_effect=mock_get_executor):
        daemon.run_once(config, imap=MagicMock())
        # With available worker and successful dispatch, alarm should NOT be raised
        assert mock_alarm.call_count == 0
        assert stall_state.is_stalled() is False

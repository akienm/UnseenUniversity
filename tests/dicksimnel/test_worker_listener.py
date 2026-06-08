"""Tests for DickSimnelWorkerListener dispatch behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from devices.dicksimnel.worker_listener import DickSimnelWorkerListener
from bus.envelope import Envelope


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_dispatch_envelope(ticket_id: str, reply_to: str = "granny.0") -> Envelope:
    """Return a dispatch envelope as Granny would send."""
    return Envelope.now(
        from_device="granny.0",
        to_device="dicksimnel.0",
        payload={"kind": "dispatch", "ticket_id": ticket_id, "reply_to": reply_to},
    )


def _make_listener(*, bus=None, device=None, poll_interval=0):
    return DickSimnelWorkerListener(
        bus=bus,
        device_mailbox="dicksimnel.0",
        granny_mailbox="granny.0",
        device=device,
        poll_interval=poll_interval,
    )


def _stub_device(*, ticket=None, should_escalate=(False, ""), result="DONE: ok"):
    dev = MagicMock()
    dev._fetch_ticket.return_value = ticket or {"id": "T-x", "title": "test", "tags": []}
    dev._should_escalate.return_value = should_escalate
    dev._run_inference.return_value = result
    dev._active_ticket = None
    return dev


# ── Dispatch — ack sent immediately ──────────────────────────────────────────


def test_dispatch_sends_ack_immediately():
    bus = MagicMock()
    device = _stub_device()
    listener = _make_listener(bus=bus, device=device)
    listener._handle_dispatch("T-foo", "granny.0")
    # First send_call should be dispatch_ack
    calls = bus.append.call_args_list
    first_payload = calls[0][0][1].payload
    assert first_payload["kind"] == "dispatch_ack"
    assert first_payload["ticket_id"] == "T-foo"


def test_dispatch_sends_started_before_inference():
    bus = MagicMock()
    device = _stub_device()
    listener = _make_listener(bus=bus, device=device)
    listener._handle_dispatch("T-foo", "granny.0")
    payloads = [c[0][1].payload["kind"] for c in bus.append.call_args_list]
    assert "dispatch_ack" in payloads
    assert "dispatch_started" in payloads
    assert payloads.index("dispatch_ack") < payloads.index("dispatch_started")


def test_dispatch_calls_run_inference():
    bus = MagicMock()
    device = _stub_device()
    listener = _make_listener(bus=bus, device=device)
    listener._handle_dispatch("T-foo", "granny.0")
    device._run_inference.assert_called_once()


# ── Missing ticket_id — silently ignored ─────────────────────────────────────


def test_missing_ticket_id_is_ignored():
    bus = MagicMock()
    device = _stub_device()
    listener = _make_listener(bus=bus, device=device)
    listener._handle_dispatch("", "granny.0")
    device._run_inference.assert_not_called()
    bus.append.assert_not_called()


# ── OR balance at floor — ticket declined ────────────────────────────────────


def test_or_balance_at_floor_triggers_decline():
    bus = MagicMock()
    device = _stub_device()
    listener = _make_listener(bus=bus, device=device)
    low_balance = {"balance": 2.0, "currency": "USD"}
    with patch("devices.dicksimnel.worker_listener.fetch_balance", return_value=low_balance):
        with patch("devices.dicksimnel.worker_listener._OR_BALANCE_FLOOR", 5.0):
            listener._handle_dispatch("T-low", "granny.0")
    # Should NOT call _run_inference
    device._run_inference.assert_not_called()
    # Should reset ticket back to sprint
    device._run_queue_cmd.assert_called_with("setstatus", "T-low", "sprint")


def test_or_balance_above_floor_proceeds():
    bus = MagicMock()
    device = _stub_device()
    listener = _make_listener(bus=bus, device=device)
    ok_balance = {"balance": 20.0, "currency": "USD"}
    with patch("devices.dicksimnel.worker_listener.fetch_balance", return_value=ok_balance):
        with patch("devices.dicksimnel.worker_listener._OR_BALANCE_FLOOR", 5.0):
            listener._handle_dispatch("T-ok", "granny.0")
    device._run_inference.assert_called_once()


def test_balance_check_unavailable_is_fail_open():
    """If balance check throws, inference proceeds — fail-open."""
    bus = MagicMock()
    device = _stub_device()
    listener = _make_listener(bus=bus, device=device)
    with patch("devices.dicksimnel.worker_listener.fetch_balance", side_effect=Exception("no network")):
        listener._handle_dispatch("T-failopen", "granny.0")
    device._run_inference.assert_called_once()


# ── HIGH-inertia escalation ───────────────────────────────────────────────────


def test_high_inertia_ticket_is_escalated_before_inference():
    bus = MagicMock()
    device = _stub_device(should_escalate=(True, "HIGH-inertia tag: Security"))
    listener = _make_listener(bus=bus, device=device)
    listener._handle_dispatch("T-sec", "granny.0")
    device._run_inference.assert_not_called()
    device._escalate_ticket.assert_called_once_with("T-sec", "HIGH-inertia tag: Security")


# ── No device wired ───────────────────────────────────────────────────────────


def test_no_device_skips_inference_gracefully():
    bus = MagicMock()
    listener = _make_listener(bus=bus, device=None)
    # Should not raise; logs warning and returns
    listener._handle_dispatch("T-nodev", "granny.0")


# ── _poll_once — bus None skips silently ─────────────────────────────────────


def test_poll_once_skips_when_bus_none():
    listener = _make_listener(bus=None)
    # Should complete without error
    listener._poll_once()


# ── _poll_once — dispatches on envelope arrival ───────────────────────────────


def test_poll_once_dispatches_on_envelope():
    bus = MagicMock()
    env = _make_dispatch_envelope("T-poll", reply_to="granny.0")
    bus.fetch_unseen.return_value = [env]
    device = _stub_device()
    listener = _make_listener(bus=bus, device=device)
    with patch.object(listener, "_handle_dispatch") as mock_handle:
        listener._poll_once()
    mock_handle.assert_called_once_with("T-poll", "granny.0")


def test_poll_once_ignores_non_dispatch_envelopes():
    bus = MagicMock()
    env = Envelope.now(
        from_device="granny.0",
        to_device="dicksimnel.0",
        payload={"kind": "heartbeat"},
    )
    bus.fetch_unseen.return_value = [env]
    device = _stub_device()
    listener = _make_listener(bus=bus, device=device)
    with patch.object(listener, "_handle_dispatch") as mock_handle:
        listener._poll_once()
    mock_handle.assert_not_called()

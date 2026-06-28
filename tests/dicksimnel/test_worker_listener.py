"""Tests for DickSimnelWorkerListener dispatch behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from unseen_university.devices.dicksimnel.worker_listener import DickSimnelWorkerListener
from unseen_university.devices.bus.envelope import Envelope


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
    with patch("unseen_university.devices.dicksimnel.worker_listener.fetch_balance", return_value=low_balance):
        with patch("unseen_university.devices.dicksimnel.worker_listener._OR_BALANCE_FLOOR", 5.0):
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
    with patch("unseen_university.devices.dicksimnel.worker_listener.fetch_balance", return_value=ok_balance):
        with patch("unseen_university.devices.dicksimnel.worker_listener._OR_BALANCE_FLOOR", 5.0):
            listener._handle_dispatch("T-ok", "granny.0")
    device._run_inference.assert_called_once()


def test_balance_check_unavailable_is_fail_open():
    """If balance check throws, inference proceeds — fail-open."""
    bus = MagicMock()
    device = _stub_device()
    listener = _make_listener(bus=bus, device=device)
    with patch("unseen_university.devices.dicksimnel.worker_listener.fetch_balance", side_effect=Exception("no network")):
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


# ── Bus reconnect watchdog ────────────────────────────────────────────────────


def test_consecutive_failures_below_threshold_no_callback():
    """Failures below threshold do not trigger on_bus_failure."""
    bus = MagicMock()
    bus.fetch_unseen.side_effect = ConnectionRefusedError("refused")
    callback = MagicMock()
    listener = DickSimnelWorkerListener(bus=bus, on_bus_failure=callback)
    from unseen_university.devices.dicksimnel.worker_listener import _FAILURE_THRESHOLD
    for _ in range(_FAILURE_THRESHOLD - 1):
        listener._poll_once()
    callback.assert_not_called()
    assert listener._consecutive_failures == _FAILURE_THRESHOLD - 1


def test_consecutive_failures_at_threshold_triggers_callback():
    """Exactly _FAILURE_THRESHOLD failures calls on_bus_failure with the listener."""
    bus = MagicMock()
    bus.fetch_unseen.side_effect = ConnectionRefusedError("refused")
    callback = MagicMock()
    listener = DickSimnelWorkerListener(bus=bus, on_bus_failure=callback)
    from unseen_university.devices.dicksimnel.worker_listener import _FAILURE_THRESHOLD
    for _ in range(_FAILURE_THRESHOLD):
        listener._poll_once()
    callback.assert_called_once_with(listener)
    assert listener._consecutive_failures == 0  # reset after callback


def test_success_resets_failure_counter():
    """A successful fetch resets _consecutive_failures to zero."""
    bus = MagicMock()
    bus.fetch_unseen.side_effect = [
        ConnectionRefusedError("refused"),
        ConnectionRefusedError("refused"),
        [],  # success
    ]
    listener = DickSimnelWorkerListener(bus=bus)
    listener._poll_once()
    listener._poll_once()
    assert listener._consecutive_failures == 2
    listener._poll_once()
    assert listener._consecutive_failures == 0


def test_no_callback_set_failures_dont_raise():
    """When on_bus_failure is None, threshold failures are logged but don't raise."""
    bus = MagicMock()
    bus.fetch_unseen.side_effect = ConnectionRefusedError("refused")
    listener = DickSimnelWorkerListener(bus=bus, on_bus_failure=None)
    from unseen_university.devices.dicksimnel.worker_listener import _FAILURE_THRESHOLD
    for _ in range(_FAILURE_THRESHOLD + 2):
        listener._poll_once()  # must not raise


# ── Shim reconnect handler ────────────────────────────────────────────────────


def test_shim_handle_bus_failure_reconnects_successfully():
    """On first failure, shim reconnects and updates listener._bus."""
    from unseen_university.devices.dicksimnel.shim import DickSimnelShim
    new_bus = MagicMock()
    shim = DickSimnelShim()
    shim._connect_bus = MagicMock(return_value=new_bus)
    listener = MagicMock()
    shim._handle_bus_failure(listener)
    assert listener._bus is new_bus
    assert shim._reconnect_count == 0  # reset on success


def test_shim_handle_bus_failure_failed_reconnect_increments_count():
    """When _connect_bus returns None, reconnect count increments."""
    from unseen_university.devices.dicksimnel.shim import DickSimnelShim
    shim = DickSimnelShim()
    shim._connect_bus = MagicMock(return_value=None)
    listener = MagicMock()
    shim._handle_bus_failure(listener)
    assert shim._reconnect_count == 1
    listener._bus = object()  # unchanged — not overwritten on failure


def test_shim_handle_bus_failure_stops_after_max_attempts():
    """After _MAX_RECONNECT_ATTEMPTS failed reconnects, shim removes availability flag."""
    from unseen_university.devices.dicksimnel.shim import DickSimnelShim, _MAX_RECONNECT_ATTEMPTS
    shim = DickSimnelShim()
    shim._connect_bus = MagicMock(return_value=None)
    shim._remove_available = MagicMock()
    listener = MagicMock()
    for _ in range(_MAX_RECONNECT_ATTEMPTS + 1):
        shim._handle_bus_failure(listener)
    shim._remove_available.assert_called_once()
    assert listener._bus is None  # silenced

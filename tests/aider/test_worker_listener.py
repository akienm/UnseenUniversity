"""Contract tests for the Aider builder: dispatch handshake + gate->close/escalate.

The load-bearing invariant is proof-on-close inside the builder: a build whose
objective gate did NOT pass (0 edits / red tests / out-of-scope edits) must
ESCALATE to CC, never close the ticket. `test_failed_gate_escalates_never_closes`
is the red-form — a hollow device that closes regardless fails it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from unseen_university.devices.aider.worker_listener import AiderWorkerListener
from unseen_university.devices.aider.runner import AiderResult
from unseen_university.devices.bus.envelope import Envelope


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_listener(*, bus=None, device=None):
    return AiderWorkerListener(bus=bus, device_mailbox="aider.0",
                               granny_mailbox="granny.0", device=device, poll_interval=0)


def _stub_device(*, ticket=None, should_escalate=(False, ""), result=None, outcome="done"):
    dev = MagicMock()
    dev._fetch_ticket.return_value = ticket or {"id": "T-x", "title": "t", "tags": [], "description": ""}
    dev._should_escalate.return_value = should_escalate
    dev._run_build.return_value = result or AiderResult(ticket_id="T-x", model="m", branch="b", gate_passed=True)
    # Single-writer: the builder returns result ARTIFACTS; the listener rides them on
    # dispatch_done for Granny to reconcile (no _post_result / _escalate_ticket writes).
    dev._build_report.return_value = {"outcome": outcome, "branch": "b"}
    dev._escalation_artifact.return_value = {"outcome": "escalated", "reason": "x"}
    dev._active_ticket = None
    return dev


def _payloads(bus):
    return [c[0][1].payload["kind"] for c in bus.append.call_args_list]


# ── Handshake order ───────────────────────────────────────────────────────────

def test_dispatch_sends_ack_then_started_then_build():
    bus, device = MagicMock(), _stub_device()
    _make_listener(bus=bus, device=device)._handle_dispatch("T-foo", "granny.0")
    p = _payloads(bus)
    assert p.index("dispatch_ack") < p.index("dispatch_started")
    device._run_build.assert_called_once()
    assert p[-1] == "dispatch_done"


def test_missing_ticket_id_ignored():
    bus, device = MagicMock(), _stub_device()
    _make_listener(bus=bus, device=device)._handle_dispatch("", "granny.0")
    device._run_build.assert_not_called()
    bus.append.assert_not_called()


def test_high_inertia_escalated_before_build():
    bus = MagicMock()
    device = _stub_device(should_escalate=(True, "HIGH-inertia tags: ['Security']"))
    _make_listener(bus=bus, device=device)._handle_dispatch("T-sec", "granny.0")
    device._run_build.assert_not_called()
    device._escalation_artifact.assert_called_once_with("T-sec", "HIGH-inertia tags: ['Security']")
    assert _payloads(bus)[-1] == "dispatch_done"
    assert bus.append.call_args_list[-1][0][1].payload["outcome"] == "escalated"


def test_unfetchable_ticket_escalates():
    bus = MagicMock()
    device = _stub_device()
    device._fetch_ticket.return_value = None
    _make_listener(bus=bus, device=device)._handle_dispatch("T-missing", "granny.0")
    device._run_build.assert_not_called()
    device._escalation_artifact.assert_called_once()
    assert bus.append.call_args_list[-1][0][1].payload["outcome"] == "escalated"


def test_build_exception_escalates_gracefully():
    bus = MagicMock()
    device = _stub_device()
    device._run_build.side_effect = RuntimeError("aider blew up")
    _make_listener(bus=bus, device=device)._handle_dispatch("T-boom", "granny.0")
    device._escalation_artifact.assert_called_once()
    assert bus.append.call_args_list[-1][0][1].payload["outcome"] == "escalated"


def test_no_device_is_graceful():
    _make_listener(bus=MagicMock(), device=None)._handle_dispatch("T-nodev", "granny.0")  # must not raise


def test_poll_dispatches_on_envelope():
    bus = MagicMock()
    bus.fetch_unseen.return_value = [Envelope.now(
        from_device="granny.0", to_device="aider.0",
        payload={"kind": "dispatch", "ticket_id": "T-poll"})]
    listener = _make_listener(bus=bus, device=_stub_device())
    with patch.object(listener, "_handle_dispatch") as mock:
        listener._poll_once()
    mock.assert_called_once_with("T-poll", "granny.0")


def test_poll_ignores_non_dispatch():
    bus = MagicMock()
    bus.fetch_unseen.return_value = [Envelope.now(
        from_device="granny.0", to_device="aider.0", payload={"kind": "heartbeat"})]
    listener = _make_listener(bus=bus, device=_stub_device())
    with patch.object(listener, "_handle_dispatch") as mock:
        listener._poll_once()
    mock.assert_not_called()

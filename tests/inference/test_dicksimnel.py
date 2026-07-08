"""Tests for DickSimnelDevice, DickSimnelShim, and DickSimnelWorkerListener."""

from __future__ import annotations

import collections
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Fake bus ──────────────────────────────────────────────────────────────────


class _FakeBus:
    def __init__(self):
        self._boxes: dict[str, list] = collections.defaultdict(list)
        self._read: dict[str, int] = collections.defaultdict(int)

    def append(self, mailbox: str, envelope) -> None:
        self._boxes[mailbox].append(envelope)

    def fetch_unseen(self, mailbox: str) -> list:
        seen = self._read[mailbox]
        new = self._boxes[mailbox][seen:]
        self._read[mailbox] = len(self._boxes[mailbox])
        return new

    def inject(self, mailbox: str, kind: str, ticket_id: str) -> None:
        from unseen_university.devices.bus.envelope import Envelope
        env = Envelope.now(
            from_device="granny.0",
            to_device=mailbox,
            payload={"kind": kind, "ticket_id": ticket_id},
        )
        self.append(mailbox, env)


# ── DickSimnelShim ────────────────────────────────────────────────────────────


class TestDickSimnelShim:
    def test_start_writes_availability_flag(self, tmp_path, monkeypatch):
        from unseen_university.devices.dicksimnel.shim import DickSimnelShim, _FLAG_DIR, _AVAILABLE_FLAG

        monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._FLAG_DIR", tmp_path)
        monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._AVAILABLE_FLAG", tmp_path / "DickSimnel.0.available.true")

        shim = DickSimnelShim()
        assert shim.start()
        assert (tmp_path / "DickSimnel.0.available.true").exists()
        shim.stop()

    def test_stop_removes_flag(self, tmp_path, monkeypatch):
        from unseen_university.devices.dicksimnel.shim import DickSimnelShim

        flag = tmp_path / "DickSimnel.0.available.true"
        monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._FLAG_DIR", tmp_path)
        monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._AVAILABLE_FLAG", flag)

        shim = DickSimnelShim()
        shim.start()
        assert flag.exists()
        shim.stop()
        assert not flag.exists()

    def test_is_blocked_reads_false_flag(self, tmp_path, monkeypatch):
        from unseen_university.devices.dicksimnel.shim import DickSimnelShim

        blocked_flag = tmp_path / "DickSimnel.0.available.false"
        monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._BLOCKED_FLAG", blocked_flag)

        shim = DickSimnelShim()
        assert not shim.is_blocked()
        blocked_flag.write_text("false")
        assert shim.is_blocked()

    def test_start_creates_and_starts_listener(self, tmp_path, monkeypatch):
        from unseen_university.devices.dicksimnel.shim import DickSimnelShim
        monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._FLAG_DIR", tmp_path)
        monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._AVAILABLE_FLAG", tmp_path / "av.true")

        shim = DickSimnelShim()
        shim._connect_bus = lambda: None  # no real bus in tests
        assert shim.start()
        assert shim._listener is not None
        shim.stop()
        assert shim._listener is None

    def test_self_test_passes_when_inference_importable(self, tmp_path, monkeypatch):
        from unseen_university.devices.dicksimnel.shim import DickSimnelShim

        monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._FLAG_DIR", tmp_path)
        shim = DickSimnelShim()
        result = shim.self_test()
        assert result["passed"]

    def test_rollback_removes_flag_if_written(self, tmp_path, monkeypatch):
        from unseen_university.devices.dicksimnel.shim import DickSimnelShim

        flag = tmp_path / "DickSimnel.0.available.true"
        monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._FLAG_DIR", tmp_path)
        monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._AVAILABLE_FLAG", flag)

        shim = DickSimnelShim()
        shim._write_available()
        assert flag.exists()
        shim.rollback()
        assert not flag.exists()


# ── DickSimnelDevice ──────────────────────────────────────────────────────────


class TestDickSimnelDevice:
    def _device(self):
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice
        d = DickSimnelDevice()
        # Replace shim with a no-op so tests don't touch filesystem or threads
        d._shim = MagicMock()
        d._shim.self_test.return_value = {"passed": True, "details": "mock"}
        d._shim.is_blocked.return_value = False
        return d

    def test_who_am_i(self):
        d = self._device()
        info = d.who_am_i()
        assert info["device_id"] == "dicksimnel"
        assert "worker" in info["agent_class"]

    def test_health_healthy(self):
        d = self._device()
        h = d.health()
        assert h["status"] == "healthy"

    def test_health_blocked(self):
        d = self._device()
        d.block("test block")
        assert d.health()["status"] == "unhealthy"
        assert "test block" in d.health()["detail"]

    def test_fetch_ticket_returns_dict(self):
        d = self._device()
        ticket = {"id": "T-abc", "title": "Fix it", "status": "dispatched"}
        show_resp = MagicMock(returncode=0, stdout=json.dumps(ticket))
        with patch("subprocess.run", return_value=show_resp):
            result = d._fetch_ticket("T-abc")
        assert result is not None
        assert result["id"] == "T-abc"

    def test_fetch_ticket_returns_none_on_fail(self):
        d = self._device()
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="not found", stdout="")):
            result = d._fetch_ticket("T-missing")
        assert result is None


# ── escalation ───────────────────────────────────────────────────────────────


class TestDickSimnelEscalation:
    def _device(self):
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice
        d = DickSimnelDevice()
        d._shim = MagicMock()
        d._shim.is_blocked.return_value = False
        return d

    def test_should_escalate_high_inertia_tags(self):
        d = self._device()
        hit, reason = d._should_escalate({"tags": ["Security", "Routing"]}, None)
        assert hit
        assert "Security" in reason

    def test_should_not_escalate_no_tags_no_result(self):
        d = self._device()
        hit, reason = d._should_escalate({"tags": ["DickSimnel"]}, None)
        assert not hit

    def test_escalate_ticket_resets_worker_and_status(self):
        d = self._device()
        d._run_queue_cmd = MagicMock(return_value=None)
        with patch("unseen_university.channel.post_to_channel"):
            d._escalate_ticket("T-test", "Security tag", "some analysis")
        calls = [str(c) for c in d._run_queue_cmd.call_args_list]
        assert any("setstatus" in c and "escalated" in c for c in calls)
        assert d._active_ticket is None
        assert d._tickets_declined == 1

    def test_escalate_ticket_writes_structured_summary(self):
        d = self._device()
        d._run_queue_cmd = MagicMock(return_value=None)
        with patch("unseen_university.channel.post_to_channel"):
            d._escalate_ticket("T-sum", "inference failed", "tried three approaches")
        append_calls = [c for c in d._run_queue_cmd.call_args_list if "append-note" in str(c)]
        assert append_calls, "append-note must be called at escalation"
        note_text = str(append_calls[0])
        assert "Escalation summary" in note_text
        assert "tried three approaches" in note_text
        assert "inference failed" in note_text
        assert "What now?" in note_text

    def test_escalate_ticket_posts_to_channel(self):
        d = self._device()
        d._run_queue_cmd = MagicMock(return_value=None)
        with patch.object(d, "_channel_event") as mock_event:
            d._escalate_ticket("T-test", "Security tag")
        mock_event.assert_called_once()
        args, kwargs = mock_event.call_args
        assert "DICKSIMNEL_ESCALATE" in args[0]
        assert "T-test" in args[0]
        assert kwargs.get("event_type") == "escalated"


# ── _post_result reliability guards ──────────────────────────────────────────


class TestPostResultGuards:
    def _device(self):
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice
        d = DickSimnelDevice()
        d._shim = MagicMock()
        d._shim.is_blocked.return_value = False
        return d

    def test_missing_done_prefix_escalates(self):
        d = self._device()
        with patch.object(d, "_escalate_ticket") as mock_esc:
            with patch.object(d, "_run_queue_cmd") as mock_cmd:
                with patch.object(d, "_channel_event"):
                    d._post_result("T-x", "I'll implement the versioned registry first.")
        mock_esc.assert_called_once()
        escalate_reason = mock_esc.call_args[0][1]
        assert "DONE:" in escalate_reason or "prefix" in escalate_reason
        mock_cmd.assert_not_called()  # close must not be called

    def test_done_prefix_closes_ticket(self):
        d = self._device()
        close_resp = {"status": "closed"}
        with patch.object(d, "_run_queue_cmd", return_value=close_resp) as mock_cmd:
            with patch.object(d, "_channel_event"):
                with patch.object(d, "_escalate_ticket") as mock_esc:
                    d._post_result("T-x", "DONE: shipped. Commit abc123. Tests pass.")
        mock_cmd.assert_called_once_with("close", "T-x", mock_cmd.call_args[0][2])
        mock_esc.assert_not_called()
        assert d._tickets_processed == 1

    def test_close_failure_escalates(self):
        d = self._device()
        with patch.object(d, "_run_queue_cmd", return_value=None):  # close fails
            with patch.object(d, "_escalate_ticket") as mock_esc:
                with patch.object(d, "_channel_event"):
                    d._post_result("T-x", "DONE: fixed it.")
        mock_esc.assert_called_once()
        reason = mock_esc.call_args[0][1]
        assert "close" in reason.lower() or "failed" in reason.lower()

    def test_close_failure_does_not_increment_processed(self):
        d = self._device()
        with patch.object(d, "_run_queue_cmd", return_value=None):
            with patch.object(d, "_escalate_ticket"):
                with patch.object(d, "_channel_event"):
                    d._post_result("T-x", "DONE: fixed.")
        assert d._tickets_processed == 0

    def test_max_turns_sentinel_escalates_with_clear_reason(self):
        d = self._device()
        with patch.object(d, "_escalate_ticket") as mock_esc:
            with patch.object(d, "_run_queue_cmd") as mock_cmd:
                with patch.object(d, "_channel_event"):
                    d._post_result("T-x", "MAX_TURNS: hit 20 turns without DONE: prefix")
        mock_esc.assert_called_once()
        reason = mock_esc.call_args[0][1]
        assert "max turns" in reason.lower(), f"reason must mention max turns, got: {reason!r}"
        mock_cmd.assert_not_called()

    def test_max_turns_sentinel_does_not_close_ticket(self):
        d = self._device()
        with patch.object(d, "_escalate_ticket"):
            with patch.object(d, "_run_queue_cmd") as mock_cmd:
                with patch.object(d, "_channel_event"):
                    d._post_result("T-x", "MAX_TURNS: hit 20 turns without DONE: prefix")
        mock_cmd.assert_not_called()
        assert d._tickets_processed == 0

    def test_already_closed_ticket_counts_as_success(self):
        """Double-close: close() returns None but show() reveals already closed → success."""
        d = self._device()

        def fake_cmd(*args):
            if args[0] == "close":
                return None  # close fails because already closed
            if args[0] == "show":
                return {"status": "closed"}
            return None

        with patch.object(d, "_run_queue_cmd", side_effect=fake_cmd):
            with patch.object(d, "_escalate_ticket") as mock_esc:
                with patch.object(d, "_channel_event"):
                    d._post_result("T-already-done", "DONE: DickSimnel already closed it")

        mock_esc.assert_not_called()
        assert d._tickets_processed == 1, "double-close must increment _tickets_processed"

    def test_already_done_ticket_counts_as_success(self):
        """Double-close with status='done' also treats as success."""
        d = self._device()

        def fake_cmd(*args):
            if args[0] == "close":
                return None
            if args[0] == "show":
                return {"status": "done"}
            return None

        with patch.object(d, "_run_queue_cmd", side_effect=fake_cmd):
            with patch.object(d, "_escalate_ticket") as mock_esc:
                with patch.object(d, "_channel_event"):
                    d._post_result("T-done", "DONE: already done")

        mock_esc.assert_not_called()
        assert d._tickets_processed == 1



# ── OR cost gate ──────────────────────────────────────────────────────────────
# The google_free worker-routing-rule tests are retired: _DEFAULT_RULES, RoutingRule,
# and route() are deleted at the router cutover. Worker→cheapest-capable selection is
# covered against resolve() in test_resolver_compose.py; there are no routing rules to
# assert the existence of anymore.


# ── DickSimnelWorkerListener ──────────────────────────────────────────────────


class TestDickSimnelWorkerListener:
    """Bus dispatch listener works tickets synchronously on envelope receipt."""

    def _listener(self, bus=None, device=None, poll_interval=999):
        from unseen_university.devices.dicksimnel.worker_listener import DickSimnelWorkerListener
        return DickSimnelWorkerListener(
            bus=bus or _FakeBus(),
            device_mailbox="dicksimnel.0",
            granny_mailbox="granny.0",
            device=device,
            poll_interval=poll_interval,
        )

    def _mock_device(self, ticket=None):
        device = MagicMock()
        t = ticket or {"id": "T-test", "title": "Test", "tags": [], "description": ""}
        device._fetch_ticket.return_value = t
        device._should_escalate.return_value = (False, "")
        device._run_inference.return_value = "DONE: done"
        return device

    def test_dispatch_sends_ack_to_granny(self):
        bus = _FakeBus()
        bus.inject("dicksimnel.0", "dispatch", "T-abc")
        device = self._mock_device()
        listener = self._listener(bus=bus, device=device)

        with patch("unseen_university.devices.dicksimnel.worker_listener.fetch_balance", return_value=None):
            listener._poll_once()

        acks = [e for e in bus._boxes.get("granny.0", [])
                if e.payload.get("kind") == "dispatch_ack"]
        assert len(acks) == 1
        assert acks[0].payload["ticket_id"] == "T-abc"

    def test_dispatch_calls_run_inference(self):
        bus = _FakeBus()
        bus.inject("dicksimnel.0", "dispatch", "T-work")
        ticket = {"id": "T-work", "title": "Do work", "tags": [], "description": "fix"}
        device = self._mock_device(ticket=ticket)
        listener = self._listener(bus=bus, device=device)

        with patch("unseen_university.devices.dicksimnel.worker_listener.fetch_balance", return_value=None):
            listener._poll_once()

        device._run_inference.assert_called_once_with(ticket)

    def test_dispatch_started_sent_before_inference(self):
        """dispatch_ack then dispatch_started are both sent, in order."""
        bus = _FakeBus()
        bus.inject("dicksimnel.0", "dispatch", "T-seq")
        device = self._mock_device()
        listener = self._listener(bus=bus, device=device)

        with patch("unseen_university.devices.dicksimnel.worker_listener.fetch_balance", return_value=None):
            listener._poll_once()

        reply_kinds = [e.payload["kind"] for e in bus._boxes.get("granny.0", [])]
        assert "dispatch_ack" in reply_kinds
        assert "dispatch_started" in reply_kinds
        assert reply_kinds.index("dispatch_ack") < reply_kinds.index("dispatch_started")

    def test_halt_envelope_stops_listener(self):
        from unseen_university.devices.bus.envelope import Envelope
        bus = _FakeBus()
        env = Envelope.now(
            from_device="granny.0",
            to_device="dicksimnel.0",
            payload={"kind": "halt"},
        )
        bus.append("dicksimnel.0", env)
        listener = self._listener(bus=bus)

        assert not listener._stop.is_set()
        listener._poll_once()
        assert listener._stop.is_set()

    def test_receive_failure_is_silent(self):
        class _BrokenBus:
            def fetch_unseen(self, _):
                raise OSError("bus down")

        listener = self._listener(bus=_BrokenBus())
        listener._poll_once()  # must not raise

    def test_balance_at_floor_declines_without_inference(self):
        bus = _FakeBus()
        bus.inject("dicksimnel.0", "dispatch", "T-floor")
        device = self._mock_device()
        listener = self._listener(bus=bus, device=device)

        with patch("unseen_university.devices.dicksimnel.worker_listener.fetch_balance",
                   return_value={"balance": 5.0}), \
             patch("unseen_university.devices.dicksimnel.worker_listener._OR_BALANCE_FLOOR", 5.0):
            listener._poll_once()

        device._run_inference.assert_not_called()
        device._channel_event.assert_called_once()
        assert "DECLINE" in device._channel_event.call_args[0][0]

    def test_balance_above_floor_proceeds_to_inference(self):
        bus = _FakeBus()
        bus.inject("dicksimnel.0", "dispatch", "T-go")
        device = self._mock_device()
        listener = self._listener(bus=bus, device=device)

        with patch("unseen_university.devices.dicksimnel.worker_listener.fetch_balance",
                   return_value={"balance": 50.0}), \
             patch("unseen_university.devices.dicksimnel.worker_listener._OR_BALANCE_FLOOR", 5.0):
            listener._poll_once()

        device._run_inference.assert_called_once()

    def test_balance_check_raises_proceeds_fail_open(self):
        bus = _FakeBus()
        bus.inject("dicksimnel.0", "dispatch", "T-nobal")
        device = self._mock_device()
        listener = self._listener(bus=bus, device=device)

        with patch("unseen_university.devices.dicksimnel.worker_listener.fetch_balance",
                   side_effect=OSError("network")):
            listener._poll_once()

        device._run_inference.assert_called_once()

    def test_high_inertia_escalates_without_inference(self):
        bus = _FakeBus()
        bus.inject("dicksimnel.0", "dispatch", "T-sec")
        ticket = {"id": "T-sec", "title": "Security fix", "tags": ["Security"], "description": ""}
        device = self._mock_device(ticket=ticket)
        device._should_escalate.return_value = (True, "HIGH-inertia tags: ['Security']")
        listener = self._listener(bus=bus, device=device)

        with patch("unseen_university.devices.dicksimnel.worker_listener.fetch_balance", return_value=None):
            listener._poll_once()

        device._run_inference.assert_not_called()
        device._escalate_ticket.assert_called_once()
        assert "Security" in device._escalate_ticket.call_args[0][1]

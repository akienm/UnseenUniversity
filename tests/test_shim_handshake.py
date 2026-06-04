"""Tests for the two-phase dispatch handshake protocol in BaseShim.

Completion criteria:
  (a) dispatch envelope → ack sent synchronously (before any prod fires)
  (b) prod fires every prod_interval seconds when deliver_fn returns False
  (c) timeout envelope sent after timeout_at seconds with no pickup
  (d) app picks up → started envelope sent and prod loop stops
"""

from __future__ import annotations

import threading
import time

import pytest

from unseen_university.shim import BaseShim, _DispatchHandshake

# ── Minimal concrete shim for testing ─────────────────────────────────────────


class _StubShim(BaseShim):
    @property
    def device_id(self) -> str:
        return "stub.0"

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        self._cancel_active_handshakes()
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        return {"passed": True, "details": "stub"}

    def rollback(self) -> None:
        pass


# ── Envelope helpers ──────────────────────────────────────────────────────────


class _FakeEnvelope:
    """Minimal stand-in for bus.envelope.Envelope."""

    def __init__(self, from_device: str, ticket_id: str) -> None:
        self.from_device = from_device
        self.payload = {"ticket_id": ticket_id}


def _dict_envelope(from_device: str, ticket_id: str) -> dict:
    """Plain-dict form of a dispatch envelope."""
    return {"from_device": from_device, "payload": {"ticket_id": ticket_id}}


# ── Criterion (a): ack sent synchronously, before any prod fires ──────────────


def test_ack_sent_before_prod():
    """dispatch_ack is the first envelope sent, synchronously inside receive_dispatch."""
    sent = []

    shim = _StubShim()
    env = _FakeEnvelope("granny", "T-abc")
    hs = shim.receive_dispatch(env, send_fn=lambda to, p: sent.append(p), prod_interval=120.0)
    # At this point receive_dispatch has returned — ack must already be in sent
    hs.cancel()

    assert sent, "no envelope sent at all"
    assert sent[0]["kind"] == "dispatch_ack"
    assert sent[0]["ticket_id"] == "T-abc"
    assert sent[0]["from_device"] == "stub.0"


def test_ack_to_correct_device():
    """Ack is addressed to the envelope's from_device."""
    recipients = []

    shim = _StubShim()
    env = _FakeEnvelope("granny.1", "T-xyz")
    hs = shim.receive_dispatch(env, send_fn=lambda to, p: recipients.append(to))
    hs.cancel()

    assert recipients[0] == "granny.1"


def test_dict_envelope_accepted():
    """receive_dispatch works with a plain-dict envelope."""
    sent = []

    shim = _StubShim()
    hs = shim.receive_dispatch(
        _dict_envelope("granny", "T-dict"),
        send_fn=lambda to, p: sent.append(p),
        prod_interval=120.0,
    )
    hs.cancel()

    assert sent[0]["kind"] == "dispatch_ack"
    assert sent[0]["ticket_id"] == "T-dict"


# ── Criterion (b): prod fires at each interval when deliver_fn returns False ──


def test_prod_fires_multiple_times():
    """deliver_fn is called repeatedly at prod_interval until cancel or pickup."""
    first_prod = threading.Event()
    second_prod = threading.Event()
    call_count = [0]

    def deliver_fn(tid):
        call_count[0] += 1
        if call_count[0] == 1:
            first_prod.set()
        elif call_count[0] >= 2:
            second_prod.set()
        return False

    hs = _DispatchHandshake(
        ticket_id="T-p",
        from_device="granny",
        device_id="stub.0",
        send_fn=lambda *_: None,
        deliver_fn=deliver_fn,
        prod_interval=0.01,
        timeout_at=5.0,
    )
    hs.start()
    assert first_prod.wait(timeout=1.0), "first prod did not fire within 1s"
    assert second_prod.wait(timeout=1.0), "second prod did not fire within 1s"
    hs.cancel()
    hs.join(timeout=0.5)


def test_prod_stops_after_cancel():
    """No more deliver_fn calls after cancel()."""
    call_count = [0]
    cancelled = threading.Event()

    def deliver_fn(tid):
        if cancelled.is_set():
            call_count[0] += 1  # should never increment
        return False

    hs = _DispatchHandshake(
        ticket_id="T-q",
        from_device="granny",
        device_id="stub.0",
        send_fn=lambda *_: None,
        deliver_fn=deliver_fn,
        prod_interval=0.005,
        timeout_at=5.0,
    )
    hs.start()
    time.sleep(0.02)  # let a couple prods fire normally
    hs.cancel()
    cancelled.set()
    hs.join(timeout=0.5)

    assert call_count[0] == 0, "deliver_fn called after cancel"


# ── Criterion (c): timeout envelope sent after timeout_at with no pickup ──────


def test_timeout_envelope_sent():
    """dispatch_timeout is sent after timeout_at seconds with no successful delivery."""
    timed_out = threading.Event()
    sent = []

    def send_fn(to, payload):
        sent.append(payload)
        if payload["kind"] == "dispatch_timeout":
            timed_out.set()

    hs = _DispatchHandshake(
        ticket_id="T-t",
        from_device="granny",
        device_id="stub.0",
        send_fn=send_fn,
        deliver_fn=lambda _: False,
        prod_interval=0.01,
        timeout_at=0.04,
    )
    hs.start()
    assert timed_out.wait(timeout=2.0), "dispatch_timeout not sent within 2s"

    timeout_msgs = [s for s in sent if s["kind"] == "dispatch_timeout"]
    assert len(timeout_msgs) == 1, "timeout sent more than once"
    assert timeout_msgs[0]["ticket_id"] == "T-t"
    assert timeout_msgs[0]["from_device"] == "stub.0"


def test_timeout_stops_prod_loop():
    """Prod loop exits after sending the timeout envelope."""
    timed_out = threading.Event()

    def send_fn(to, payload):
        if payload["kind"] == "dispatch_timeout":
            timed_out.set()

    hs = _DispatchHandshake(
        ticket_id="T-ts",
        from_device="granny",
        device_id="stub.0",
        send_fn=send_fn,
        deliver_fn=lambda _: False,
        prod_interval=0.01,
        timeout_at=0.04,
    )
    hs.start()
    timed_out.wait(timeout=2.0)
    hs.join(timeout=1.0)

    assert not hs.is_active


def test_no_started_after_timeout():
    """After a timeout, no dispatch_started is ever emitted."""
    sent = []
    timed_out = threading.Event()

    def send_fn(to, payload):
        sent.append(payload["kind"])
        if payload["kind"] == "dispatch_timeout":
            timed_out.set()

    hs = _DispatchHandshake(
        ticket_id="T-nsa",
        from_device="granny",
        device_id="stub.0",
        send_fn=send_fn,
        deliver_fn=lambda _: False,
        prod_interval=0.01,
        timeout_at=0.04,
    )
    hs.start()
    timed_out.wait(timeout=2.0)
    hs.join(timeout=1.0)

    assert "dispatch_started" not in sent


# ── Criterion (d): app picks up → started sent and loop stops ─────────────────


def test_started_sent_on_pickup():
    """dispatch_started is sent when deliver_fn returns True."""
    started = threading.Event()
    sent = []

    def send_fn(to, payload):
        sent.append(payload)
        if payload["kind"] == "dispatch_started":
            started.set()

    hs = _DispatchHandshake(
        ticket_id="T-s",
        from_device="granny",
        device_id="stub.0",
        send_fn=send_fn,
        deliver_fn=lambda _: True,
        prod_interval=0.01,
        timeout_at=5.0,
    )
    hs.start()
    assert started.wait(timeout=1.0), "dispatch_started not sent within 1s"

    started_msgs = [s for s in sent if s["kind"] == "dispatch_started"]
    assert len(started_msgs) == 1
    assert started_msgs[0]["ticket_id"] == "T-s"
    assert started_msgs[0]["from_device"] == "stub.0"


def test_prod_loop_stops_after_pickup():
    """Prod loop exits after dispatch_started is sent."""
    started = threading.Event()

    def send_fn(to, payload):
        if payload["kind"] == "dispatch_started":
            started.set()

    hs = _DispatchHandshake(
        ticket_id="T-lp",
        from_device="granny",
        device_id="stub.0",
        send_fn=send_fn,
        deliver_fn=lambda _: True,
        prod_interval=0.01,
        timeout_at=5.0,
    )
    hs.start()
    started.wait(timeout=1.0)
    hs.join(timeout=1.0)

    assert not hs.is_active


def test_pickup_after_initial_rejection():
    """deliver_fn returns False on first call, True on second — started fires on second."""
    started = threading.Event()
    call_count = [0]

    def deliver_fn(tid):
        call_count[0] += 1
        return call_count[0] >= 2  # first call refuses, second accepts

    def send_fn(to, payload):
        if payload["kind"] == "dispatch_started":
            started.set()

    hs = _DispatchHandshake(
        ticket_id="T-ld",
        from_device="granny",
        device_id="stub.0",
        send_fn=send_fn,
        deliver_fn=deliver_fn,
        prod_interval=0.01,
        timeout_at=5.0,
    )
    hs.start()
    assert started.wait(timeout=2.0), "started not sent after second prod"
    assert call_count[0] == 2


def test_no_timeout_after_pickup():
    """No dispatch_timeout is sent when the app picks up before timeout_at."""
    sent_kinds = []
    started = threading.Event()

    def send_fn(to, payload):
        sent_kinds.append(payload["kind"])
        if payload["kind"] == "dispatch_started":
            started.set()

    hs = _DispatchHandshake(
        ticket_id="T-nt",
        from_device="granny",
        device_id="stub.0",
        send_fn=send_fn,
        deliver_fn=lambda _: True,
        prod_interval=0.01,
        timeout_at=5.0,
    )
    hs.start()
    started.wait(timeout=1.0)
    hs.join(timeout=1.0)

    assert "dispatch_timeout" not in sent_kinds


# ── BaseShim.receive_dispatch integration ────────────────────────────────────


def test_receive_dispatch_tracks_handshake():
    """receive_dispatch registers the handshake in _active_handshakes."""
    shim = _StubShim()
    env = _FakeEnvelope("granny", "T-track")
    hs = shim.receive_dispatch(env, send_fn=lambda *_: None, prod_interval=120.0)
    assert "T-track" in shim._active_handshakes
    hs.cancel()


def test_cancel_active_handshakes_stops_all():
    """_cancel_active_handshakes cancels all registered handshakes."""
    shim = _StubShim()

    for i in range(3):
        env = _FakeEnvelope("granny", f"T-{i}")
        shim.receive_dispatch(env, send_fn=lambda *_: None, prod_interval=120.0)

    shim._cancel_active_handshakes()

    assert len(shim._active_handshakes) == 0


def test_stop_cancels_handshakes():
    """stop() on a shim that overrides _cancel_active_handshakes cancels in-flight work."""
    shim = _StubShim()
    env = _FakeEnvelope("granny", "T-stop")
    hs = shim.receive_dispatch(env, send_fn=lambda *_: None, prod_interval=120.0)

    shim.stop()

    assert not hs.is_active

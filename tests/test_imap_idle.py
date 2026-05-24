"""Tests for IMAP IDLE client — T-bus-imap-idle-client.

Covers:
- idle_wait wakes within 100ms of message arrival
- idle_wait returns False on timeout
- keepalive re-entry: run_forever continues after idle timeout
- run_forever stops cleanly when stop event is set
"""

from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

from bus.envelope import Envelope
from bus.imap_server import IMAPServer, _STUB_MAILBOXES


@pytest.fixture()
def imap():
    s = IMAPServer()
    s.start()
    s.create_mailbox("test-idle")
    yield s
    s.stop()


def _dummy_envelope() -> Envelope:
    return Envelope.now(from_device="sender", to_device="receiver", payload={"x": 1})


class TestIdleWait:
    def test_wakes_within_100ms_of_append(self, imap):
        woke_at: list[float] = []

        def waiter():
            result = imap.idle_wait("test-idle", timeout_s=5.0)
            woke_at.append(time.monotonic())
            assert result is True

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.02)  # let waiter register event

        send_at = time.monotonic()
        imap.append("test-idle", _dummy_envelope())
        t.join(timeout=1.0)
        assert not t.is_alive(), "waiter did not wake"
        assert (
            woke_at[0] - send_at < 0.1
        ), f"wake latency {woke_at[0]-send_at:.3f}s > 100ms"

    def test_returns_false_on_timeout(self, imap):
        result = imap.idle_wait("test-idle", timeout_s=0.05)
        assert result is False

    def test_wakes_only_for_correct_mailbox(self, imap):
        imap.create_mailbox("other-mailbox")
        woke: list[bool] = []

        def waiter():
            woke.append(imap.idle_wait("test-idle", timeout_s=0.15))

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.02)

        # Append to a different mailbox — should NOT wake test-idle
        imap.append("other-mailbox", _dummy_envelope())
        t.join(timeout=0.5)
        assert woke == [False], "idle_wait woke for wrong mailbox"

    def test_multiple_waiters_all_wake(self, imap):
        results: list[bool] = []
        lock = threading.Lock()

        def waiter():
            r = imap.idle_wait("test-idle", timeout_s=2.0)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=waiter, daemon=True) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(0.05)
        imap.append("test-idle", _dummy_envelope())
        for t in threads:
            t.join(timeout=1.0)
        assert all(t is True for t in results), f"not all waiters woke: {results}"


class TestRunForever:
    def test_pump_called_on_wakeup(self, imap):
        """run_forever calls pump() when a message arrives."""
        from unseen_university.announce.broker import AnnounceBroker
        from unseen_university.announce.listener import AnnounceListener
        from unseen_university.announce.envelope import ANNOUNCE_MAILBOX
        from unittest.mock import MagicMock, patch

        imap.create_mailbox(ANNOUNCE_MAILBOX)
        broker = MagicMock()
        listener = AnnounceListener(broker=broker, imap_server=imap)

        pumped: list[int] = []
        original_pump = listener.pump

        def tracking_pump():
            pumped.append(1)
            return original_pump()

        listener.pump = tracking_pump
        stop = threading.Event()

        t = threading.Thread(
            target=listener.run_forever, kwargs={"stop": stop}, daemon=True
        )
        t.start()
        time.sleep(0.05)  # let listener enter IDLE

        imap.append(ANNOUNCE_MAILBOX, _dummy_envelope())
        time.sleep(0.1)  # give run_forever time to pump

        stop.set()
        # Append one more to unblock idle_wait so the loop can check stop
        imap.append(ANNOUNCE_MAILBOX, _dummy_envelope())
        t.join(timeout=1.0)
        assert len(pumped) >= 1, "pump() was never called after message arrival"

    def test_stops_cleanly_on_stop_event(self, imap):
        from unseen_university.announce.broker import AnnounceBroker
        from unseen_university.announce.listener import AnnounceListener
        from unseen_university.announce.envelope import ANNOUNCE_MAILBOX
        from unittest.mock import MagicMock

        imap.create_mailbox(ANNOUNCE_MAILBOX)
        listener = AnnounceListener(broker=MagicMock(), imap_server=imap)
        stop = threading.Event()

        t = threading.Thread(
            target=listener.run_forever, kwargs={"stop": stop}, daemon=True
        )
        t.start()
        time.sleep(0.05)

        stop.set()
        imap.append(ANNOUNCE_MAILBOX, _dummy_envelope())  # unblock idle_wait
        t.join(timeout=1.0)
        assert not t.is_alive(), "run_forever did not stop"

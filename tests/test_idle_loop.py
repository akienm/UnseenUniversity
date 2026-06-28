"""
AnnounceIdleLoop tests — IDLE-driven push wakeup against the IMAP stub.
"""

from __future__ import annotations

import os
import threading
import time

# Test mode must be set BEFORE bus.imap_server is imported.
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

import pytest

from unseen_university.announce import AnnounceIdleLoop
from unseen_university.devices.bus.envelope import Envelope
from unseen_university.devices.bus.imap_server import IMAPServer

WATCH_MAILBOX = "test-idle-watch"


@pytest.fixture()
def server():
    s = IMAPServer()
    s.start()
    s.create_mailbox(WATCH_MAILBOX)
    yield s
    s.stop()


def _make_loop(server, callback) -> AnnounceIdleLoop:
    return AnnounceIdleLoop(
        host=server.host,
        port=server.port,
        mailbox=WATCH_MAILBOX,
        callback=callback,
    )


def _await(predicate, timeout=2.0, interval=0.02) -> bool:
    """Poll predicate until true or timeout. Returns the final value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ── Wakeup behavior ──────────────────────────────────────────────────────────


def test_idle_loop_wakes_callback_on_append(server):
    fired = threading.Event()
    counter = {"n": 0}

    def on_exists():
        counter["n"] += 1
        fired.set()

    loop = _make_loop(server, on_exists)
    loop.start()
    try:
        # Loop fires once at startup (catchup) — wait for that to settle.
        assert _await(lambda: counter["n"] >= 1, timeout=2.0)
        fired.clear()
        baseline = counter["n"]

        # Now post — IDLE should wake within ~100ms.
        env = Envelope.now(from_device="test", to_device=WATCH_MAILBOX, payload={})
        server.append(WATCH_MAILBOX, env)

        assert _await(
            lambda: counter["n"] > baseline, timeout=2.0
        ), "callback did not fire on EXISTS within 2s"
    finally:
        loop.stop()


def test_idle_loop_fires_callback_at_startup(server):
    """Catchup pump on startup means agents that posted before IDLE
    registered still get processed."""
    counter = {"n": 0}

    # Pre-load a message before starting the loop.
    env = Envelope.now(from_device="test", to_device=WATCH_MAILBOX, payload={})
    server.append(WATCH_MAILBOX, env)

    def cb():
        counter["n"] += 1

    loop = _make_loop(server, cb)
    loop.start()
    try:
        assert _await(lambda: counter["n"] >= 1, timeout=2.0)
    finally:
        loop.stop()


# ── Shutdown ──────────────────────────────────────────────────────────────────


def test_stop_unblocks_idle_cleanly(server):
    """A loop sitting in IDLE on an empty mailbox must exit when stop()
    closes the socket — within the join timeout."""
    counter = {"n": 0}

    def cb():
        counter["n"] += 1

    loop = _make_loop(server, cb)
    loop.start()
    try:
        # Let the startup-catchup callback fire then settle into IDLE.
        assert _await(lambda: counter["n"] >= 1, timeout=2.0)
        time.sleep(0.1)  # ensure we're back in IDLE waiting
    finally:
        start = time.monotonic()
        loop.stop(timeout=2.0)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"stop() took {elapsed:.2f}s — exceeded join timeout"


def test_double_start_is_idempotent(server):
    counter = {"n": 0}

    def cb():
        counter["n"] += 1

    loop = _make_loop(server, cb)
    loop.start()
    loop.start()  # second call must not raise or spawn a second thread
    try:
        # Single thread → exactly one startup-catchup, eventually one EXISTS
        assert _await(lambda: counter["n"] >= 1, timeout=2.0)
    finally:
        loop.stop()


# ── Error tolerance ─────────────────────────────────────────────────────────


def test_callback_exception_does_not_kill_loop(server):
    """If the callback raises, the loop should log and keep waking."""
    counter = {"n": 0}

    def cb():
        counter["n"] += 1
        if counter["n"] == 1:
            raise RuntimeError("boom")  # raised on startup-catchup

    loop = _make_loop(server, cb)
    loop.start()
    try:
        # Wait for the failing startup callback.
        assert _await(lambda: counter["n"] >= 1, timeout=2.0)
        # Loop should still wake on the next EXISTS.
        env = Envelope.now(from_device="test", to_device=WATCH_MAILBOX, payload={})
        server.append(WATCH_MAILBOX, env)
        assert _await(
            lambda: counter["n"] >= 2, timeout=2.0
        ), "loop died after callback exception"
    finally:
        loop.stop()

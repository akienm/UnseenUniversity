"""T-boredom-lock-and-enable-audit — cognition/boredom.py concurrency.

Pass-2 Area 4 P1-4.1: _traversal_log was a module-level dict mutated
without a lock. Main-loop-only today, but the moment pe_chain or any
background source calls record_traversal, the race window opens. This
test pins the lock-guarded invariant.
"""

from __future__ import annotations

import os
import threading

import pytest

from unseen_university.devices.igor.cognition import boredom as br


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("IGOR_BOREDOM_ENABLED", "true")
    br.reset()
    yield
    br.reset()


def test_concurrent_record_no_crash(enabled):
    """Simultaneous record_traversal calls should not raise or corrupt the log."""

    def worker(prefix):
        for i in range(200):
            br.record_traversal(f"{prefix}_{i % 10}")

    threads = [threading.Thread(target=worker, args=(p,)) for p in ("a", "b", "c", "d")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # No assertions on counts — just no crash. Total recorded entries are
    # bounded by the rolling-window prune; we just want the structure intact.
    lvl = br.boredom_level()
    assert 0.0 <= lvl <= 1.0


def test_concurrent_read_during_write(enabled):
    """boredom_level should return a coherent snapshot while writes happen."""
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            br.record_traversal(f"node_{i % 5}")
            i += 1

    def reader():
        for _ in range(100):
            lvl = br.boredom_level()
            assert 0.0 <= lvl <= 1.0

    writers = [threading.Thread(target=writer) for _ in range(3)]
    readers = [threading.Thread(target=reader) for _ in range(3)]
    for t in writers + readers:
        t.start()
    for t in readers:
        t.join()
    stop.set()
    for t in writers:
        t.join()


def test_reset_is_also_locked(enabled):
    """reset must not tear the structure mid-read."""
    for i in range(50):
        br.record_traversal(f"node_{i}")

    def resetter():
        for _ in range(20):
            br.reset()

    def reader():
        for _ in range(50):
            br.boredom_level()

    threads = [threading.Thread(target=resetter) for _ in range(2)] + [
        threading.Thread(target=reader) for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # After all resets, log should be empty
    assert br.boredom_level() == 0.0

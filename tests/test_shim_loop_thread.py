"""
tests/test_shim_loop_thread.py — ShimLoopThread, the ONE shim-owned poll loop.

ONE daemon structure (T-collapse-daemons-to-ground-loop): a device's periodic work is
a daemon thread its shim owns, not a standalone ``__main__`` + ``while True`` daemon.
This replaces the old ``granny.daemon._wait_for_work`` interval-polling wakeup — the
loop now ticks then waits ``interval`` inside ``ShimLoopThread``.

Covers:
- tick() runs each cycle; on_cycle(cycle_number) runs after each tick.
- a raising tick does not kill the loop (one bad cycle is logged, not fatal).
- stop() halts the thread (is_alive() → False).
"""

from __future__ import annotations

import threading


def test_loop_ticks_then_stops():
    from unseen_university.shim import ShimLoopThread

    ticked = threading.Event()
    calls = []

    def tick():
        calls.append(1)
        ticked.set()

    loop = ShimLoopThread("test", tick, interval=0.01)
    loop.start()
    assert ticked.wait(2.0), "tick should run at least once"
    loop.stop()
    assert not loop.is_alive()
    assert len(calls) >= 1


def test_on_cycle_receives_incrementing_cycle_number():
    from unseen_university.shim import ShimLoopThread

    seen = []
    got_two = threading.Event()

    def on_cycle(cycle):
        seen.append(cycle)
        if len(seen) >= 2:
            got_two.set()

    loop = ShimLoopThread("test", lambda: None, interval=0.01, on_cycle=on_cycle)
    loop.start()
    assert got_two.wait(2.0), "on_cycle should fire at least twice"
    loop.stop()
    # cycle numbers are 1-based and strictly increasing
    assert seen[0] == 1
    assert seen[1] == 2


def test_raising_tick_does_not_kill_loop():
    from unseen_university.shim import ShimLoopThread

    calls = []
    twice = threading.Event()

    def tick():
        calls.append(1)
        if len(calls) >= 2:
            twice.set()
        raise RuntimeError("boom")  # every tick raises

    loop = ShimLoopThread("test", tick, interval=0.01)
    loop.start()
    assert twice.wait(2.0), "loop must keep ticking despite a raising tick"
    loop.stop()
    assert not loop.is_alive()

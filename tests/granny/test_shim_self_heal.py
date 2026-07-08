"""Tests for GrannyShim — in-process dispatch loop, demand-started off the queue.

ONE daemon structure (T-collapse-daemons-to-ground-loop): the shim no longer watches a
PID file or relaunches a tmux subprocess. Its watchdog demand-starts an in-process
``ShimLoopThread`` when sprint work is pending; ``self_test`` reports that thread's
liveness.
"""

from __future__ import annotations

from unittest.mock import patch


class _FakeLoop:
    """Stand-in for ShimLoopThread with a settable liveness."""

    def __init__(self, alive: bool = False):
        self._alive = alive
        self.stopped = False

    def is_alive(self) -> bool:
        return self._alive

    def stop(self) -> None:
        self.stopped = True
        self._alive = False


class TestSelfTest:
    def test_no_loop_returns_not_passed(self):
        from unseen_university.devices.granny.shim import GrannyShim

        shim = GrannyShim()
        result = shim.self_test()
        assert result["passed"] is False
        assert "not running" in result["details"]

    def test_live_loop_returns_passed(self):
        from unseen_university.devices.granny.shim import GrannyShim

        shim = GrannyShim()
        shim._loop = _FakeLoop(alive=True)
        result = shim.self_test()
        assert result["passed"] is True
        assert "running" in result["details"]

    def test_dead_loop_returns_not_passed(self):
        from unseen_university.devices.granny.shim import GrannyShim

        shim = GrannyShim()
        shim._loop = _FakeLoop(alive=False)
        result = shim.self_test()
        assert result["passed"] is False


class TestWatchdogDemandStart:
    def test_pending_work_with_no_loop_starts_it(self):
        from unseen_university.devices.granny.shim import GrannyShim

        shim = GrannyShim()
        started = []
        shim._start_dispatch_loop = lambda: started.append(True) or setattr(
            shim, "_loop", _FakeLoop(alive=True)
        )
        shim._has_pending_tickets = lambda: True

        shim._watchdog_loop_once()
        assert started, "expected the dispatch loop to be demand-started"
        assert shim._relaunch_count == 1

    def test_no_pending_work_does_not_start(self):
        from unseen_university.devices.granny.shim import GrannyShim

        shim = GrannyShim()
        started = []
        shim._start_dispatch_loop = lambda: started.append(True)
        shim._has_pending_tickets = lambda: False

        shim._watchdog_loop_once()
        assert not started, "expected no start with no pending tickets"
        assert shim._relaunch_count == 0

    def test_live_loop_does_not_restart(self):
        from unseen_university.devices.granny.shim import GrannyShim

        shim = GrannyShim()
        shim._loop = _FakeLoop(alive=True)
        started = []
        shim._start_dispatch_loop = lambda: started.append(True)
        shim._has_pending_tickets = lambda: True

        shim._watchdog_loop_once()
        assert not started, "expected no restart when the loop is already alive"

    def test_dead_loop_with_pending_work_restarts(self):
        from unseen_university.devices.granny.shim import GrannyShim

        shim = GrannyShim()
        shim._loop = _FakeLoop(alive=False)  # started once, then died
        started = []
        shim._start_dispatch_loop = lambda: started.append(True) or setattr(
            shim, "_loop", _FakeLoop(alive=True)
        )
        shim._has_pending_tickets = lambda: True

        shim._watchdog_loop_once()
        assert started, "expected a restart when the loop died with work pending"
        assert shim._relaunch_count == 1


class TestStartStop:
    def test_start_launches_watchdog_thread(self):
        from unseen_university.devices.granny.shim import GrannyShim

        shim = GrannyShim()
        assert shim._watchdog_thread is None
        shim.start()
        assert shim._watchdog_thread is not None
        assert shim._watchdog_thread.is_alive()
        shim._watchdog_stop.set()  # clean up

    def test_start_idempotent(self):
        from unseen_university.devices.granny.shim import GrannyShim

        shim = GrannyShim()
        shim.start()
        thread1 = shim._watchdog_thread
        shim.start()  # second call — should reuse existing thread
        assert shim._watchdog_thread is thread1
        shim._watchdog_stop.set()

    def test_stop_sets_event_and_stops_loop(self):
        from unseen_university.devices.granny.shim import GrannyShim

        shim = GrannyShim()
        shim.start()
        shim._loop = _FakeLoop(alive=True)
        assert not shim._watchdog_stop.is_set()
        shim.stop()
        assert shim._watchdog_stop.is_set()
        assert shim._loop.stopped is True

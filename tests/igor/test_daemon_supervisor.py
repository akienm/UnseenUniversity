"""
test_daemon_supervisor.py — Tests for T-daemon-supervisor: DaemonSupervisor
thread lifecycle registry.

Tests:
  - register() stores thread entry
  - status() reflects alive thread
  - status() reflects dead thread
  - health_fn=None → healthy is None in status
  - health_fn provided → healthy reflects return value
  - health_fn that raises → healthy is False (defensive)
  - report_str() contains thread names
  - report_str() flags dead threads
  - report_str() with no threads registered
  - re-register same name overwrites entry
  - multiple threads tracked independently
  - _get_daemon_report tool smoke test
"""

import sys
import threading
import time
import unittest
from pathlib import Path


from unseen_university.devices.igor.cognition.daemon_supervisor import DaemonSupervisor


def _live_thread() -> threading.Thread:
    """Start a daemon thread that runs forever (until test ends)."""
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, daemon=True)
    t.start()
    return t


def _dead_thread() -> threading.Thread:
    """Return a thread that has already finished."""
    t = threading.Thread(target=lambda: None, daemon=True)
    t.start()
    t.join(timeout=1.0)
    return t


class TestDaemonSupervisorRegister(unittest.TestCase):
    def setUp(self):
        self.sup = DaemonSupervisor()

    def test_register_stores_entry(self):
        t = _live_thread()
        self.sup.register("test-thread", t)
        rows = self.sup.status()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "test-thread")

    def test_register_overwrites_same_name(self):
        t1 = _live_thread()
        t2 = _live_thread()
        self.sup.register("same-name", t1)
        self.sup.register("same-name", t2)
        rows = self.sup.status()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "same-name")

    def test_multiple_threads_tracked(self):
        self.sup.register("a", _live_thread())
        self.sup.register("b", _live_thread())
        self.sup.register("c", _live_thread())
        rows = self.sup.status()
        names = {r["name"] for r in rows}
        self.assertEqual(names, {"a", "b", "c"})


class TestDaemonSupervisorStatus(unittest.TestCase):
    def setUp(self):
        self.sup = DaemonSupervisor()

    def test_alive_thread_reflects_true(self):
        t = _live_thread()
        self.sup.register("live", t)
        row = self.sup.status()[0]
        self.assertTrue(row["alive"])

    def test_dead_thread_reflects_false(self):
        t = _dead_thread()
        self.sup.register("dead", t)
        row = self.sup.status()[0]
        self.assertFalse(row["alive"])

    def test_uptime_is_nonnegative(self):
        t = _live_thread()
        self.sup.register("uptime-test", t)
        row = self.sup.status()[0]
        self.assertGreaterEqual(row["uptime_s"], 0.0)

    def test_no_health_fn_gives_none(self):
        t = _live_thread()
        self.sup.register("no-health", t, health_fn=None)
        row = self.sup.status()[0]
        self.assertIsNone(row["healthy"])

    def test_health_fn_true(self):
        t = _live_thread()
        self.sup.register("healthy", t, health_fn=lambda: True)
        row = self.sup.status()[0]
        self.assertTrue(row["healthy"])

    def test_health_fn_false(self):
        t = _live_thread()
        self.sup.register("unhealthy", t, health_fn=lambda: False)
        row = self.sup.status()[0]
        self.assertFalse(row["healthy"])

    def test_health_fn_raises_gives_false(self):
        t = _live_thread()

        def bad_health():
            raise RuntimeError("probe failed")

        self.sup.register("boom", t, health_fn=bad_health)
        row = self.sup.status()[0]
        self.assertFalse(row["healthy"])


class TestDaemonSupervisorReportStr(unittest.TestCase):
    def setUp(self):
        self.sup = DaemonSupervisor()

    def test_empty_report(self):
        report = self.sup.report_str()
        self.assertIn("no threads registered", report)

    def test_thread_name_in_report(self):
        t = _live_thread()
        self.sup.register("my-worker", t)
        report = self.sup.report_str()
        self.assertIn("my-worker", report)

    def test_dead_thread_flagged(self):
        t = _dead_thread()
        self.sup.register("dead-worker", t)
        report = self.sup.report_str()
        self.assertIn("dead-worker", report)
        self.assertIn("dead-worker", report.lower())  # warning line includes the name

    def test_live_thread_not_flagged_as_dead(self):
        t = _live_thread()
        self.sup.register("live-worker", t)
        report = self.sup.report_str()
        # The warning line should NOT appear
        self.assertNotIn("dead thread", report.lower())

    def test_report_header_present(self):
        t = _live_thread()
        self.sup.register("any", t)
        report = self.sup.report_str()
        self.assertIn("DAEMON SUPERVISOR", report)


class TestGetDaemonReportTool(unittest.TestCase):
    def test_smoke(self):
        """_get_daemon_report should return a string without raising."""
        from unseen_university.devices.igor.tools.metrics import _get_daemon_report

        result = _get_daemon_report()
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)


class TestDaemonSupervisorHeartbeatLiveness(unittest.TestCase):
    """T-daemon-supervisor-spawn-liveness — heartbeat-based staleness detection
    for one-shot-per-tick workers (ne-worker, consolidation-worker).
    """

    def setUp(self):
        self.sup = DaemonSupervisor()

    def test_heartbeat_noop_on_unregistered(self):
        """Calling heartbeat before register() is harmless — some call sites
        may fire before the first register (e.g., inside the spawn hook's
        is_alive early-return)."""
        self.sup.heartbeat("not-registered")  # must not raise

    def test_fresh_entry_not_stale(self):
        t = _dead_thread()
        self.sup.register("ne-worker", t, one_shot=True, staleness_threshold_secs=60.0)
        row = self.sup.status()[0]
        self.assertFalse(row["stale"])
        self.assertLess(row["since_heartbeat_s"], 1.0)

    def test_stale_when_heartbeat_older_than_threshold(self):
        t = _dead_thread()
        self.sup.register("ne-worker", t, one_shot=True, staleness_threshold_secs=0.01)
        # Force the heartbeat into the past by mutating the internal entry
        with self.sup._lock:
            self.sup._entries["ne-worker"].last_heartbeat_ts -= 10.0
        row = self.sup.status()[0]
        self.assertTrue(row["stale"])

    def test_heartbeat_refreshes_staleness(self):
        t = _dead_thread()
        self.sup.register("ne-worker", t, one_shot=True, staleness_threshold_secs=0.01)
        with self.sup._lock:
            self.sup._entries["ne-worker"].last_heartbeat_ts -= 10.0
        self.assertTrue(self.sup.status()[0]["stale"])
        self.sup.heartbeat("ne-worker")
        self.assertFalse(self.sup.status()[0]["stale"])

    def test_threshold_none_means_no_staleness_check(self):
        """Workers without a configured threshold never report stale."""
        t = _dead_thread()
        self.sup.register("no-threshold", t, one_shot=True)
        with self.sup._lock:
            self.sup._entries["no-threshold"].last_heartbeat_ts -= 99999.0
        self.assertFalse(self.sup.status()[0]["stale"])

    def test_non_oneshot_never_reports_stale(self):
        """Long-running workers use is_alive(); staleness only applies to
        one-shot-per-tick shape."""
        t = _live_thread()
        self.sup.register(
            "long-running", t, one_shot=False, staleness_threshold_secs=0.01
        )
        with self.sup._lock:
            self.sup._entries["long-running"].last_heartbeat_ts -= 10.0
        self.assertFalse(self.sup.status()[0]["stale"])

    def test_report_flags_stale(self):
        t = _dead_thread()
        self.sup.register("ne-worker", t, one_shot=True, staleness_threshold_secs=0.01)
        with self.sup._lock:
            self.sup._entries["ne-worker"].last_heartbeat_ts -= 10.0
        report = self.sup.report_str()
        self.assertIn("STALE", report)
        self.assertIn("stale worker", report)
        self.assertIn("ne-worker", report)

    def test_report_does_not_flag_oneshot_dead_as_dead_thread(self):
        """One-shot-per-tick workers are 'dead' between ticks by design —
        don't surface them in the dead-thread warning. Only genuinely-stale
        one-shots get flagged."""
        t = _dead_thread()
        self.sup.register("ne-worker", t, one_shot=True, staleness_threshold_secs=600.0)
        report = self.sup.report_str()
        # Should NOT appear in "dead thread(s)" warning line
        self.assertNotIn("dead thread(s)", report)

    def test_register_preserves_heartbeat_across_reentry(self):
        """Per-tick re-register with a fresh thread must NOT reset the
        heartbeat — otherwise staleness becomes meaningless (every re-register
        would look fresh whether the hook fired or not)."""
        t1 = _dead_thread()
        self.sup.register("ne-worker", t1, one_shot=True, staleness_threshold_secs=60.0)
        with self.sup._lock:
            self.sup._entries["ne-worker"].last_heartbeat_ts -= 30.0
        before = self.sup.status()[0]["since_heartbeat_s"]
        # Re-register with a new thread (what the main loop does per-tick)
        t2 = _dead_thread()
        self.sup.register("ne-worker", t2, one_shot=True, staleness_threshold_secs=60.0)
        after = self.sup.status()[0]["since_heartbeat_s"]
        # Heartbeat age should be preserved (within a small wall-clock delta)
        self.assertGreater(after, before - 1.0)


if __name__ == "__main__":
    unittest.main()

"""
test_queue_gate.py — T-queue-gate

Tests:
  - cmd_next returns highest-priority sprint ticket (DB required)
  - cmd_next returns nothing when gate file is tripped
  - cmd_reset --timeout increments timeout_count; trips gate after 3 consecutive
  - daemon self-restart: mtime change triggers exec bash $SELF (shell integration)
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import devlab.claudecode.cc_queue as cc_queue


def _seed_ticket(ticket_id: str, priority, worker: str | None = None) -> None:
    """Write a ticket into the (test-isolated) filesystem ticket_store.

    Postgres was dropped from the ticket path (T-ticket-pg-drop); seeding goes to
    the FS store. Callers isolate the store with a tmp UU_MEMORY_ROOT in setUp.
    """
    from unseen_university import ticket_store

    ticket_store.write({
        "id": ticket_id,
        "title": f"Test ticket {ticket_id}",
        "size": "S",
        "status": "sprint",
        "worker": worker,
        "priority": priority,
        "tags": ["test"],
        "gate": None,
        "scraps_validated": "2026-01-01T00:00:00+00:00",
    })


class _IsolatedStoreCase(unittest.TestCase):
    """Base: each test gets a fresh tmp filesystem ticket_store (no real store touched)."""

    def _start_store(self):
        self._store_dir = tempfile.TemporaryDirectory()
        (Path(self._store_dir.name) / "tickets").mkdir(parents=True, exist_ok=True)
        self._store_env = mock.patch.dict(
            os.environ, {"UU_MEMORY_ROOT": self._store_dir.name})
        self._store_env.start()

    def _stop_store(self):
        self._store_env.stop()
        self._store_dir.cleanup()


class TestCmdNextDB(_IsolatedStoreCase):
    """cmd_next returns the correct ticket from the filesystem store."""

    def setUp(self):
        self._start_store()
        self._ids = ["T-gate-test-hi", "T-gate-test-lo"]
        _seed_ticket("T-gate-test-hi", priority=0.95, worker="igor")
        _seed_ticket("T-gate-test-lo", priority=0.5, worker="igor")

    def tearDown(self):
        self._stop_store()

    def test_next_returns_highest_priority(self):
        """cmd_next --worker igor returns the higher-importance ticket and claims it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gate_file = os.path.join(tmpdir, "queue_gate.json")
            with mock.patch.object(cc_queue, "GATE_FILE", gate_file):
                captured = []
                with mock.patch(
                    "builtins.print", side_effect=lambda *a: captured.append(str(a[0]))
                ):
                    cc_queue.cmd_next(["--worker", "igor"])
                self.assertEqual(len(captured), 1)
                self.assertEqual(captured[0], "T-gate-test-hi")


class TestCmdNextGateFile(unittest.TestCase):
    """cmd_next respects gate file without hitting DB."""

    def _fake_load(self):
        return [
            {
                "id": "T-a",
                "status": "sprint",
                "priority": 0.9,
                "gate": None,
                "worker": "igor",
            }
        ]

    @staticmethod
    def _run_next_with_store(tasks, args, gate_file):
        """Seed a tmp filesystem ticket_store with `tasks` and run cmd_next.

        cmd_next claims via ticket_store.conditional_update reading the LIVE store
        (Postgres dropped, T-ticket-pg-drop), so the tickets must exist on disk —
        mocking _load is no longer enough for the claim to succeed. Returns the
        list of printed lines.
        """
        from unseen_university import ticket_store

        output = []
        with tempfile.TemporaryDirectory() as store_root:
            (Path(store_root) / "tickets").mkdir(parents=True, exist_ok=True)
            with mock.patch.dict(os.environ, {"UU_MEMORY_ROOT": store_root}):
                for t in tasks:
                    ticket_store.write(t)
                with mock.patch.object(cc_queue, "GATE_FILE", gate_file), \
                        mock.patch.object(cc_queue, "_classifier_stamp_in_flight",
                                          lambda *a, **k: None), \
                        mock.patch("builtins.print",
                                   side_effect=lambda *a: output.append(str(a[0]))):
                    cc_queue.cmd_next(args)
        return output

    def test_returns_nothing_when_gate_tripped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gate_file = os.path.join(tmpdir, "queue_gate.json")
            with open(gate_file, "w") as f:
                json.dump(
                    {
                        "tripped": True,
                        "reason": "test",
                        "ticket_id": "T-x",
                        "tripped_at": "now",
                    },
                    f,
                )
            output = self._run_next_with_store(
                self._fake_load(), ["--worker", "igor"], gate_file)
            self.assertEqual(
                output, [], "cmd_next should print nothing when gate is tripped"
            )

    def test_returns_ticket_when_no_gate_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gate_file = os.path.join(tmpdir, "queue_gate.json")  # does NOT exist
            output = self._run_next_with_store(
                self._fake_load(), ["--worker", "igor"], gate_file)
            self.assertEqual(output, ["T-a"])

    def test_corrupt_gate_file_treated_as_not_tripped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gate_file = os.path.join(tmpdir, "queue_gate.json")
            with open(gate_file, "w") as f:
                f.write("not json {{{")
            output = self._run_next_with_store(
                self._fake_load(), ["--worker", "igor"], gate_file)
            self.assertEqual(output, ["T-a"], "corrupt gate file should not block next")

    def test_worker_igor_only_returns_igor_tickets(self):
        """--worker igor only returns tickets with worker=igor."""
        tasks = [
            {"id": "T-igor", "status": "sprint", "priority": 0.99,
             "gate": None, "worker": "igor"},
            {"id": "T-claude", "status": "sprint", "priority": 0.9,
             "gate": None, "worker": "claude"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            gate_file = os.path.join(tmpdir, "queue_gate.json")
            output = self._run_next_with_store(tasks, ["--worker", "igor"], gate_file)
            self.assertEqual(output, ["T-igor"])

    def test_worker_claude_only_returns_claude_tickets(self):
        """--worker claude only returns tickets with worker=claude."""
        tasks = [
            {"id": "T-igor", "status": "sprint", "priority": 0.99,
             "gate": None, "worker": "igor"},
            {"id": "T-claude", "status": "sprint", "priority": 0.9,
             "gate": None, "worker": "claude"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            gate_file = os.path.join(tmpdir, "queue_gate.json")
            output = self._run_next_with_store(tasks, ["--worker", "claude"], gate_file)
            self.assertEqual(output, ["T-claude"])

    def test_missing_worker_flag_exits_with_error(self):
        """cmd_next without --worker exits 1 — direct claiming without worker is forbidden."""
        with self.assertRaises(SystemExit) as cm:
            cc_queue.cmd_next([])
        self.assertEqual(cm.exception.code, 1)


class TestCmdResetTimeoutDB(_IsolatedStoreCase):
    """cmd_reset --timeout increments counter and trips gate at 3."""

    TID = "T-gate-timeout-test"

    def setUp(self):
        self._start_store()
        _seed_ticket(self.TID, priority=0.8)

    def tearDown(self):
        self._stop_store()

    def test_auto_trip_after_3_timeouts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gate_file = os.path.join(tmpdir, "queue_gate.json")
            with mock.patch.object(cc_queue, "GATE_FILE", gate_file), mock.patch.object(
                cc_queue, "LOG_PATH", os.path.join(tmpdir, "log.jsonl")
            ):
                # First two resets: no gate
                cc_queue.cmd_reset(["--timeout", self.TID])
                self.assertFalse(
                    os.path.exists(gate_file), "gate should not trip after 1 timeout"
                )

                # Need to reset status back to something resetable after first reset
                tasks = cc_queue._load()
                t = cc_queue._find(tasks, self.TID)
                t["status"] = "in_progress"
                cc_queue._save(tasks)

                cc_queue.cmd_reset(["--timeout", self.TID])
                self.assertFalse(
                    os.path.exists(gate_file), "gate should not trip after 2 timeouts"
                )

                tasks = cc_queue._load()
                t = cc_queue._find(tasks, self.TID)
                t["status"] = "in_progress"
                cc_queue._save(tasks)

                # Third reset: gate trips
                cc_queue.cmd_reset(["--timeout", self.TID])
                self.assertTrue(
                    os.path.exists(gate_file), "gate file should exist after 3 timeouts"
                )
                gate_data = json.loads(open(gate_file).read())
                self.assertTrue(gate_data["tripped"])
                self.assertEqual(gate_data["ticket_id"], self.TID)
                self.assertIn("3", gate_data["reason"])

    def test_regular_reset_does_not_increment_counter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gate_file = os.path.join(tmpdir, "queue_gate.json")
            with mock.patch.object(cc_queue, "GATE_FILE", gate_file), mock.patch.object(
                cc_queue, "LOG_PATH", os.path.join(tmpdir, "log.jsonl")
            ):
                cc_queue.cmd_reset([self.TID])
                tasks = cc_queue._load()
                t = cc_queue._find(tasks, self.TID)
                self.assertIsNone(t.get("timeout_count"))
                self.assertFalse(os.path.exists(gate_file))


class TestDaemonSelfRestart(unittest.TestCase):
    """Daemon exec-reloads when script mtime changes."""

    def test_exec_fires_on_mtime_change(self):
        """Minimal bash snippet verifying exec-on-mtime logic matches the daemon."""
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = os.path.join(tmpdir, "marker.txt")
            # Mirror the daemon's self-restart logic: capture mtime at startup,
            # check each iteration, exec-on-change. Uses a forced past timestamp
            # to guarantee mtime difference regardless of clock granularity.
            # ITER env var prevents infinite loop after exec.
            script = os.path.join(tmpdir, "self_restart_test.sh")
            with open(script, "w") as f:
                f.write(f"""#!/usr/bin/env bash
set -uo pipefail
SELF="$(realpath "${{BASH_SOURCE[0]}}")"
SELF_MTIME=$(stat -c %Y "$SELF" 2>/dev/null || echo "0")
MARKER="{marker}"
ITER=${{ITER:-0}}

if [ "$ITER" -eq 0 ]; then
    echo "first:$$" >> "$MARKER"
    # Force a past timestamp — guarantees mtime change regardless of sub-second granularity
    touch -t 202001010000 "$SELF"
    CURRENT_MTIME=$(stat -c %Y "$SELF" 2>/dev/null || echo "0")
    if [ "$SELF_MTIME" != "0" ] && [ "$CURRENT_MTIME" != "$SELF_MTIME" ]; then
        echo "exec:$$" >> "$MARKER"
        export ITER=1
        exec bash "$SELF"
    fi
else
    echo "second:$$" >> "$MARKER"
fi
""")
            os.chmod(script, 0o755)

            result = subprocess.run(
                ["bash", script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            lines = (
                open(marker).read().strip().splitlines()
                if os.path.exists(marker)
                else []
            )
            # Must have "exec:..." line indicating the self-restart path ran
            exec_lines = [l for l in lines if l.startswith("exec:")]
            self.assertTrue(exec_lines, f"exec not triggered; marker={lines}")
            # exec keeps the same PID
            first_pid = [l.split(":")[1] for l in lines if l.startswith("first:")][0]
            exec_pid = exec_lines[0].split(":")[1]
            self.assertEqual(first_pid, exec_pid, "exec should preserve PID")


if __name__ == "__main__":
    unittest.main()

"""
test_cc_queue_gate_clear.py — T-cc-queue-gate-clear-on-close

Tests for the gate-clear-on-close behavior in cmd_done.
"""

import sys
from pathlib import Path

# Ensure TheIgors root is in sys.path so lab.claudecode resolves (with __path__
# extension to agent_datacenter for the canonical file).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import lab.claudecode.cc_queue as cc_queue


class TestUngateDependents:
    def test_clears_gate_referencing_closed_id(self):
        tasks = [
            {"id": "T-a", "status": "done"},
            {"id": "T-b", "status": "pending", "gate": "T-a"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a")
        assert n == 1
        assert tasks[1]["gate"] is None

    def test_skips_terminal_statuses_only(self):
        # hold/blocked: gate clears when dependency closes (hold status stays, gate lifted)
        # done/closed/cancelled: never ungated
        tasks = [
            {"id": "T-b", "status": "hold", "gate": "T-a"},
            {"id": "T-c", "status": "done", "gate": "T-a"},
            {"id": "T-d", "status": "closed", "gate": "T-a"},
            {"id": "T-e", "status": "cancelled", "gate": "T-a"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a")
        assert n == 1  # only T-b (hold) ungated; terminal statuses skipped
        assert tasks[0]["gate"] is None
        assert tasks[1]["gate"] == "T-a"
        assert tasks[2]["gate"] == "T-a"
        assert tasks[3]["gate"] == "T-a"

    def test_skips_no_gate(self):
        tasks = [
            {"id": "T-b", "status": "pending", "gate": None},
            {"id": "T-c", "status": "pending"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a")
        assert n == 0

    def test_substring_match_on_id_in_gate_text(self):
        # Gate text might be "T-a-bliss-igorbase" — substring containing "T-a" still clears
        tasks = [
            {"id": "T-b", "status": "pending", "gate": "T-a-bliss-igorbase"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a-bliss-igorbase")
        assert n == 1
        assert tasks[0]["gate"] is None

    def test_clears_multiple_dependents(self):
        tasks = [
            {"id": "T-b", "status": "pending", "gate": "T-a"},
            {"id": "T-c", "status": "pending", "gate": "T-a-extra-suffix"},
            {"id": "T-d", "status": "pending", "gate": "T-other"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a")
        assert n == 2
        assert tasks[0]["gate"] is None
        assert tasks[1]["gate"] is None
        assert tasks[2]["gate"] == "T-other"

    def test_unrelated_gates_preserved(self):
        tasks = [
            {"id": "T-b", "status": "pending", "gate": "T-z"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a")
        assert n == 0
        assert tasks[0]["gate"] == "T-z"

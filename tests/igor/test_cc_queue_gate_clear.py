"""
test_cc_queue_gate_clear.py — T-cc-queue-gate-clear-on-close

Tests for the gate-clear-on-close behavior in cmd_done.
"""

import sys
from pathlib import Path

# Ensure TheIgors root is in sys.path so lab.claudecode resolves (with __path__
# extension to unseen_university for the canonical file).
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
        # done/closed/cancelled: never ungated.
        # The closed predecessor (T-a) must be present-and-terminal in the task
        # list: _ungate_dependents now defers to _gate_clear, which releases a
        # gate only when EVERY referenced predecessor is terminal (multi-dep fix,
        # T-gate-clear-source-consolidation).
        tasks = [
            {"id": "T-a", "status": "closed"},
            {"id": "T-b", "status": "hold", "gate": "T-a"},
            {"id": "T-c", "status": "done", "gate": "T-a"},
            {"id": "T-d", "status": "closed", "gate": "T-a"},
            {"id": "T-e", "status": "cancelled", "gate": "T-a"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a")
        assert n == 1  # only T-b (hold) ungated; terminal statuses skipped
        assert tasks[1]["gate"] is None
        assert tasks[2]["gate"] == "T-a"
        assert tasks[3]["gate"] == "T-a"
        assert tasks[4]["gate"] == "T-a"

    def test_skips_no_gate(self):
        tasks = [
            {"id": "T-b", "status": "pending", "gate": None},
            {"id": "T-c", "status": "pending"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a")
        assert n == 0

    def test_exact_full_id_gate_clears(self):
        # A gate that IS the full id "T-a-bliss-igorbase" clears when that exact
        # ticket closes. (Token matching is exact, not substring — see the
        # substring-safety test below for the bug this guards against.)
        tasks = [
            {"id": "T-a-bliss-igorbase", "status": "closed"},
            {"id": "T-b", "status": "pending", "gate": "T-a-bliss-igorbase"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a-bliss-igorbase")
        assert n == 1
        assert tasks[1]["gate"] is None

    def test_substring_id_does_not_ungate(self):
        # Regression guard for the consolidated fix: closing "T-a" must NOT ungate
        # a ticket gated on "T-a-extra-suffix" (the old `closed_id in gate_text`
        # substring test wrongly released it). Token membership is exact.
        tasks = [
            {"id": "T-a", "status": "closed"},
            {"id": "T-c", "status": "pending", "gate": "T-a-extra-suffix"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a")
        assert n == 0
        assert tasks[1]["gate"] == "T-a-extra-suffix"

    def test_clears_multiple_dependents(self):
        # Closing T-a clears every ticket whose gate references T-a as an exact
        # token (T-b), leaves substring siblings gated (T-c on T-a-extra-suffix),
        # and leaves unrelated gates alone (T-d on T-other).
        tasks = [
            {"id": "T-a", "status": "closed"},
            {"id": "T-b", "status": "pending", "gate": "T-a"},
            {"id": "T-c", "status": "pending", "gate": "T-a-extra-suffix"},
            {"id": "T-d", "status": "pending", "gate": "T-other"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a")
        assert n == 1
        assert tasks[1]["gate"] is None
        assert tasks[2]["gate"] == "T-a-extra-suffix"
        assert tasks[3]["gate"] == "T-other"

    def test_unrelated_gates_preserved(self):
        tasks = [
            {"id": "T-b", "status": "pending", "gate": "T-z"},
        ]
        n = cc_queue._ungate_dependents(tasks, "T-a")
        assert n == 0
        assert tasks[0]["gate"] == "T-z"


class TestGateClearDateParsing:
    """_gate_clear date-format gate support (T-gate-date-parsing)."""

    def test_future_date_blocks(self):
        # A gate string starting with a future YYYY-MM-DD should block (return False)
        clear = cc_queue._gate_clear("2099-01-01", [])
        assert clear is False

    def test_past_date_clears(self):
        # A gate string starting with a past date should clear (return True)
        clear = cc_queue._gate_clear("2000-01-01", [])
        assert clear is True

    def test_today_clears(self):
        from datetime import date

        today = date.today().isoformat()
        clear = cc_queue._gate_clear(today, [])
        assert clear is True

    def test_date_with_trailing_text_blocks_when_future(self):
        # "2099-06-19 — 30 days after T-skill-telemetry-rollup closed; ..."
        # The date is future so it should block even though text contains a ticket id
        tasks = [{"id": "T-skill-telemetry-rollup", "status": "closed"}]
        clear = cc_queue._gate_clear(
            "2099-06-19 — 30 days after T-skill-telemetry-rollup closed", tasks
        )
        assert clear is False

    def test_unknown_format_fails_closed(self):
        # A gate string that is neither a date nor a known ticket id → blocked
        clear = cc_queue._gate_clear("some narrative text with no ticket or date", [])
        assert clear is False

    def test_null_gate_clears(self):
        clear = cc_queue._gate_clear(None, [])
        assert clear is True

    def test_ticket_id_gate_still_works(self):
        # Legacy ticket-id gate still works when no date prefix present
        tasks = [{"id": "T-dep", "status": "closed"}]
        clear = cc_queue._gate_clear("T-dep", tasks)
        assert clear is True

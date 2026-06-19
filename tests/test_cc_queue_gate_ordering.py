"""Tests for cc_queue gate/dependency ordering — T-granny-ticket-dependency-ordering (BUILD half).

Covers the multi-predecessor all-closed fix in _gate_clear and
_ungate_dependents. These are pure functions over an in-memory tasks list, so
no DB is required.

Test plan (from the ticket):
  1. single-predecessor ungates on that predecessor's close (regression);
  2. two-predecessor gate ('T-A T-C') does NOT ungate when only A closes, DOES
     ungate once both are terminal (the bug fix);
  3. substring safety: closing T-foo must not ungate a ticket gated on T-foo-bar;
  4. date tokens are respected (a future date keeps the gate closed);
  5. AR-009: ungate / ungate_deferred decisions are logged.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devlab", "claudecode"))

import cc_queue  # noqa: E402


def _task(tid, status="sprint", gate=None):
    return {"id": tid, "status": status, "gate": gate, "title": tid}


# ── _gate_clear: multi-predecessor semantics ──────────────────────────────────


def test_gate_clear_null_gate():
    assert cc_queue._gate_clear(None, []) is True
    assert cc_queue._gate_clear("", []) is True


def test_gate_clear_single_predecessor():
    tasks = [_task("T-A", status="closed")]
    assert cc_queue._gate_clear("T-A", tasks) is True
    tasks = [_task("T-A", status="sprint")]
    assert cc_queue._gate_clear("T-A", tasks) is False


def test_gate_clear_two_predecessors_requires_all():
    # Only A terminal → still blocked.
    tasks = [_task("T-A", status="closed"), _task("T-C", status="sprint")]
    assert cc_queue._gate_clear("T-A T-C", tasks) is False
    # Both terminal → clear.
    tasks = [_task("T-A", status="closed"), _task("T-C", status="cancelled")]
    assert cc_queue._gate_clear("T-A T-C", tasks) is True


def test_gate_clear_prose_with_embedded_id():
    tasks = [_task("T-devlab-schema-create", status="done")]
    gate = "gates on T-devlab-schema-create (schema must exist first)"
    assert cc_queue._gate_clear(gate, tasks) is True


def test_gate_clear_embedded_uppercase_id():
    tasks = [_task("T-consequence-D-constraints", status="closed")]
    assert cc_queue._gate_clear("T-consequence-D-constraints", tasks) is True


def test_gate_clear_future_date_blocks():
    assert cc_queue._gate_clear("2999-01-01", []) is False
    assert cc_queue._gate_clear("2000-01-01", []) is True


def test_gate_clear_id_and_future_date_blocks():
    # Even with the predecessor terminal, a future date keeps it blocked.
    tasks = [_task("T-A", status="closed")]
    assert cc_queue._gate_clear("2999-01-01 T-A", tasks) is False


def test_gate_clear_unknown_format_fails_closed():
    assert cc_queue._gate_clear("waiting on something", []) is False


def test_gate_clear_missing_id_fails_closed():
    # Referenced id not in the queue → conservative (blocked), not released.
    assert cc_queue._gate_clear("T-nonexistent", []) is False


# ── _ungate_dependents: close-triggered release ───────────────────────────────


def test_ungate_single_predecessor_regression():
    """Single-dep chain still flows on close (must not regress)."""
    succ = _task("T-cc-walk-02", gate="T-cc-walk-01")
    pred = _task("T-cc-walk-01", status="closed")
    tasks = [pred, succ]
    with patch.object(cc_queue, "_log"):
        n = cc_queue._ungate_dependents(tasks, "T-cc-walk-01")
    assert n == 1
    assert succ["gate"] is None


def test_ungate_multi_predecessor_holds_until_all_closed():
    """The bug fix: gate='T-A T-C' must NOT release when only A closes."""
    succ = _task("T-D", gate="T-A T-C")
    a = _task("T-A", status="closed")
    c = _task("T-C", status="sprint")
    tasks = [a, c, succ]
    with patch.object(cc_queue, "_log"):
        n = cc_queue._ungate_dependents(tasks, "T-A")
    assert n == 0
    assert succ["gate"] == "T-A T-C"  # still gated

    # Now C closes too → release.
    c["status"] = "closed"
    with patch.object(cc_queue, "_log"):
        n = cc_queue._ungate_dependents(tasks, "T-C")
    assert n == 1
    assert succ["gate"] is None


def test_ungate_substring_safety():
    """Closing T-foo must NOT ungate a ticket gated on T-foo-bar."""
    succ = _task("T-dep", gate="T-foo-bar")
    foo = _task("T-foo", status="closed")
    bar = _task("T-foo-bar", status="sprint")
    tasks = [foo, bar, succ]
    with patch.object(cc_queue, "_log"):
        n = cc_queue._ungate_dependents(tasks, "T-foo")
    assert n == 0
    assert succ["gate"] == "T-foo-bar"


def test_ungate_logs_deferred_decision():
    """AR-009: a held multi-dep gate emits an ungate_deferred log entry."""
    succ = _task("T-D", gate="T-A T-C")
    tasks = [_task("T-A", status="closed"), _task("T-C", status="sprint"), succ]
    logged = []
    with patch.object(cc_queue, "_log", side_effect=lambda e: logged.append(e)):
        cc_queue._ungate_dependents(tasks, "T-A")
    actions = [e.get("action") for e in logged]
    assert "ungate_deferred" in actions


def test_ungate_logs_on_release():
    """AR-009: a successful release emits ungate_on_close with the count."""
    succ = _task("T-D", gate="T-A")
    tasks = [_task("T-A", status="closed"), succ]
    logged = []
    with patch.object(cc_queue, "_log", side_effect=lambda e: logged.append(e)):
        cc_queue._ungate_dependents(tasks, "T-A")
    release = [e for e in logged if e.get("action") == "ungate_on_close"]
    assert release and release[0]["ungated_count"] == 1

"""Proof for T-ungate-status-flip.

Clearing a ticket's gate must make it dispatchable in ONE step. Before the fix,
`ungate` (manual) and `_ungate_dependents` (on close) cleared the gate but left
status `dependency`, so Granny/query-ticket never surfaced the ticket — three
freshly-ungated impl tickets needed a manual `setstatus sprint` (observed
2026-07-07, commit 8939527c). These tests go RED with AssertionError on the
pre-fix behavior (status stays `dependency`) and GREEN once the flip lands.
`hold` and multi-predecessor gates are left untouched.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devlab", "claudecode"))

import cc_queue  # noqa: E402


def _task(tid, status="sprint", gate=None):
    return {"id": tid, "status": status, "gate": gate, "title": tid}


def _run_ungate(tasks, tid, extra=None):
    log_calls = []
    with (
        patch.object(cc_queue, "_load", return_value=tasks),
        patch.object(cc_queue, "_save") as mock_save,
        patch.object(cc_queue, "_log", side_effect=lambda x: log_calls.append(x)),
    ):
        cc_queue.cmd_ungate([tid] + (extra or []))
        saved = mock_save.call_args[0][0] if mock_save.called else tasks
    return saved, log_calls


# ── The proof node: manual ungate flips a gate-only dependency ticket ──────────


def test_manual_ungate_flips_dependency_to_sprint():
    tasks = [_task("T-impl", status="dependency", gate="T-design")]
    saved, logs = _run_ungate(tasks, "T-impl")
    t = next(x for x in saved if x["id"] == "T-impl")
    assert t["gate"] is None
    assert t["status"] == "sprint", "cleared gate must leave the ticket claimable"
    assert any(e.get("action") == "ungate_status_flip" for e in logs)


def test_manual_ungate_leaves_hold_untouched():
    tasks = [_task("T-paused", status="hold", gate="T-design")]
    saved, logs = _run_ungate(tasks, "T-paused")
    t = next(x for x in saved if x["id"] == "T-paused")
    assert t["gate"] is None
    assert t["status"] == "hold", "hold is Akien-only; ungate must not flip it"
    assert not any(e.get("action") == "ungate_status_flip" for e in logs)


def test_ungate_on_close_flips_dependency_but_not_multi_predecessor():
    # T-a: gated only on the closing predecessor -> flips. T-b: also gated on an
    # OPEN predecessor -> gate stays, status stays dependency.
    tasks = [
        _task("T-a", status="dependency", gate="T-closer"),
        _task("T-b", status="dependency", gate="T-closer T-still-open"),
        _task("T-closer", status="closed"),
        _task("T-still-open", status="sprint"),
    ]
    log_calls = []
    with patch.object(cc_queue, "_log", side_effect=lambda x: log_calls.append(x)):
        cc_queue._ungate_dependents(tasks, "T-closer")
    a = next(x for x in tasks if x["id"] == "T-a")
    b = next(x for x in tasks if x["id"] == "T-b")
    assert a["gate"] is None and a["status"] == "sprint"
    assert b["gate"] == "T-closer T-still-open" and b["status"] == "dependency"


def test_flip_helper_ignores_ticket_that_still_has_a_gate():
    # Defensive: the helper must not flip while a gate remains (caller clears first).
    t = _task("T-x", status="dependency", gate="T-blocker")
    assert cc_queue._flip_ungated_to_sprint(t) is False
    assert t["status"] == "dependency"

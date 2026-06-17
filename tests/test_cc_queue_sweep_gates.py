"""Tests for cc_queue sweep-gates (T-day-close-gate-sweep).

Covers: elapsed-date gate cleared, future-date gate preserved, mixed
id+date gate preserved while id is open, idempotent re-run is a no-op.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devlab", "claudecode"))

import cc_queue  # noqa: E402


def _task(tid, status="sprint", gate=None):
    return {"id": tid, "status": status, "gate": gate, "title": tid}


def _run_sweep(tasks, extra_args=None):
    """Run cmd_sweep_gates over an in-memory task list; return (tasks, log_calls)."""
    log_calls = []
    with (
        patch.object(cc_queue, "_load", return_value=tasks),
        patch.object(cc_queue, "_save") as mock_save,
        patch.object(cc_queue, "_log", side_effect=lambda x: log_calls.append(x)),
    ):
        cc_queue.cmd_sweep_gates(extra_args or [])
        saved = mock_save.call_args[0][0] if mock_save.called else tasks
    return saved, log_calls


# ── Core semantics ────────────────────────────────────────────────────────────


def test_elapsed_date_gate_is_cleared():
    tasks = [_task("T-foo", gate="2000-01-01")]
    saved, logs = _run_sweep(tasks)
    assert saved[0]["gate"] is None
    assert any(e["action"] == "sweep_gate_cleared" for e in logs)


def test_future_date_gate_is_preserved():
    tasks = [_task("T-bar", gate="2999-12-31")]
    saved, logs = _run_sweep(tasks)
    assert saved[0]["gate"] == "2999-12-31"
    assert not logs


def test_mixed_id_and_elapsed_date_gate_is_preserved():
    # An open predecessor id is present — sweep must NOT clear even if the date elapsed.
    tasks = [
        _task("T-dep", gate="2000-01-01 T-blocker"),
        _task("T-blocker", status="sprint"),
    ]
    saved, logs = _run_sweep(tasks)
    assert saved[0]["gate"] == "2000-01-01 T-blocker"
    assert not logs


def test_multiple_elapsed_dates_all_cleared_in_one_pass():
    tasks = [
        _task("T-a", gate="2000-01-01"),
        _task("T-b", gate="2001-06-15"),
        _task("T-c", gate="2999-01-01"),  # future — must stay
    ]
    saved, logs = _run_sweep(tasks)
    assert saved[0]["gate"] is None
    assert saved[1]["gate"] is None
    assert saved[2]["gate"] == "2999-01-01"
    assert sum(1 for e in logs if e["action"] == "sweep_gate_cleared") == 2


def test_closed_ticket_gate_not_touched():
    tasks = [_task("T-done", status="closed", gate="2000-01-01")]
    saved, logs = _run_sweep(tasks)
    assert saved[0]["gate"] == "2000-01-01"
    assert not logs


def test_idempotent_cleared_gate_is_noop():
    tasks = [_task("T-x", gate=None)]
    saved, logs = _run_sweep(tasks)
    assert saved[0]["gate"] is None
    assert not logs


# ── AR-009: each cleared gate is logged ──────────────────────────────────────


def test_log_entry_contains_gate_was_field():
    tasks = [_task("T-log", gate="2020-03-15")]
    _, logs = _run_sweep(tasks)
    assert logs[0]["gate_was"] == "2020-03-15"
    assert logs[0]["id"] == "T-log"


# ── Dry-run ───────────────────────────────────────────────────────────────────


def test_dry_run_does_not_write():
    tasks = [_task("T-dry", gate="2000-01-01")]
    with (
        patch.object(cc_queue, "_load", return_value=tasks),
        patch.object(cc_queue, "_save") as mock_save,
        patch.object(cc_queue, "_log"),
    ):
        cc_queue.cmd_sweep_gates(["--dry-run"])
        assert not mock_save.called
    # Gate untouched on the in-memory object
    assert tasks[0]["gate"] == "2000-01-01"

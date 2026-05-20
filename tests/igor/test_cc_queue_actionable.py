"""Tests for cc_queue.py --actionable flag (T-queue-actionable-view)."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import lab.claudecode.cc_queue as q


def _t(**kw):
    base = {
        "id": "T-x",
        "title": "test",
        "status": "sprint",
        "worker": None,
        "gate": None,
        "priority": 0.5,
        "size": "S",
        "tags": [],
        "decision_id": None,
        "description": "",
        "result": None,
        "claimed_at": None,
        "created_at": None,
        "completed_at": None,
        "github_issue": None,
    }
    base.update(kw)
    return base


def _list_actionable(tasks):
    """Run cmd_list --actionable with tasks mocked; return stdout lines."""
    with patch.object(q, "_load", return_value=tasks):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            q.cmd_list(["--actionable"])
        return buf.getvalue()


class TestGateClear:
    def test_null_gate_is_clear(self):
        assert q._gate_clear(None, []) is True

    def test_empty_string_gate_is_clear(self):
        assert q._gate_clear("", []) is True

    def test_gate_on_closed_ticket_is_clear(self):
        tasks = [_t(id="T-dep", status="closed")]
        assert q._gate_clear("T-dep", tasks) is True

    def test_gate_on_done_ticket_is_clear(self):
        tasks = [_t(id="T-dep", status="done")]
        assert q._gate_clear("T-dep", tasks) is True

    def test_gate_on_open_ticket_is_not_clear(self):
        tasks = [_t(id="T-dep", status="sprint")]
        assert q._gate_clear("T-dep", tasks) is False

    def test_gate_on_unknown_ticket_is_not_clear(self):
        # Referenced ticket not in tasks list → opaque condition → not clear
        assert q._gate_clear("T-unknown", []) is False


class TestActionableFilter:
    def test_includes_sprint_null_gate(self):
        tasks = [_t(id="T-a", status="sprint", worker="claude")]
        out = _list_actionable(tasks)
        assert "T-a" in out

    def test_includes_design_ticket(self):
        tasks = [_t(id="T-a", status="design", worker=None)]
        out = _list_actionable(tasks)
        assert "T-a" in out

    def test_includes_awaiting_approval_ticket(self):
        tasks = [_t(id="T-a", status="awaiting_approval", worker=None)]
        out = _list_actionable(tasks)
        assert "T-a" in out

    def test_excludes_worker_igor(self):
        tasks = [_t(id="T-a", status="sprint", worker="igor")]
        out = _list_actionable(tasks)
        assert "T-a" not in out

    def test_excludes_gated_open_ticket(self):
        tasks = [
            _t(id="T-blocker", status="sprint", worker=None),
            _t(id="T-a", status="sprint", worker=None, gate="T-blocker"),
        ]
        out = _list_actionable(tasks)
        assert "T-a" not in out

    def test_includes_ticket_whose_gate_is_closed(self):
        tasks = [
            _t(id="T-dep", status="closed", worker=None),
            _t(id="T-a", status="sprint", worker="claude", gate="T-dep"),
        ]
        out = _list_actionable(tasks)
        assert "T-a" in out

    def test_excludes_non_actionable_status(self):
        for status in ("hold", "cancelled", "triage", "in_progress"):
            tasks = [_t(id="T-a", status=status, worker=None)]
            out = _list_actionable(tasks)
            assert "T-a" not in out, f"status={status} should be excluded"

    def test_unfiltered_list_unchanged(self):
        """list without --actionable still shows all non-gated tickets."""
        tasks = [
            _t(id="T-a", status="sprint", worker="igor"),
            _t(id="T-b", status="design", worker=None),
        ]
        with patch.object(q, "_load", return_value=tasks):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                q.cmd_list([])
            out = buf.getvalue()
        assert "T-a" in out
        assert "T-b" in out

"""Tests for cc_queue.py cmd_next --max-difficulty flag (T-queue-next-difficulty)."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import devlab.claudecode.cc_queue as q


def _t(**kw):
    base = {
        "id": "T-x",
        "title": "test",
        "status": "sprint",
        "worker": "igor",
        "gate": None,
        "priority": 0.5,
        "size": "S",
        "tags": [],
        "decision_id": None,
        "description": "",
        "result": None,
        "dispatched_at": None,
        "created_at": None,
        "completed_at": None,
        "github_issue": None,
        "target_difficulty": 1,
    }
    base.update(kw)
    return base


class TestNextTicketIdForWorkerMaxDifficulty:
    def _next(self, tasks, worker="igor", max_difficulty=None):
        with patch.object(q, "_load", return_value=tasks), patch(
            "os.path.exists", return_value=False
        ):
            return q.next_ticket_id_for_worker(worker, max_difficulty)

    def test_no_filter_returns_any_igor_ticket(self):
        tasks = [_t(id="T-a", target_difficulty=2)]
        assert self._next(tasks, max_difficulty=None) == "T-a"

    def test_max_difficulty_1_skips_difficulty_2_ticket(self):
        tasks = [_t(id="T-a", target_difficulty=2)]
        assert self._next(tasks, max_difficulty=1) is None

    def test_max_difficulty_5_returns_difficulty_2_ticket(self):
        tasks = [_t(id="T-a", target_difficulty=2)]
        assert self._next(tasks, max_difficulty=5) == "T-a"

    def test_ticket_with_no_difficulty_field_treated_as_1(self):
        t = _t(id="T-a")
        del t["target_difficulty"]
        assert self._next([t], max_difficulty=1) == "T-a"

    def test_max_difficulty_1_returns_difficulty_1_ticket(self):
        tasks = [_t(id="T-a", target_difficulty=1)]
        assert self._next(tasks, max_difficulty=1) == "T-a"

    def test_picks_highest_priority_within_cap(self):
        tasks = [
            _t(id="T-low", target_difficulty=1, priority=0.3),
            _t(id="T-high", target_difficulty=1, priority=0.9),
            _t(id="T-over", target_difficulty=2, priority=1.0),
        ]
        result = self._next(tasks, max_difficulty=1)
        assert result == "T-high"
        assert result != "T-over"


class TestCmdNextMaxDifficultyFlag:
    def _cmd_next(self, args, tasks):
        # cmd_next claims atomically via ticket_store.conditional_update, which
        # reads the LIVE on-disk store (Postgres dropped, T-ticket-pg-drop) — so
        # seed a tmp filesystem store with the tasks rather than mocking _load.
        import os
        import tempfile

        from unseen_university import ticket_store

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tickets").mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"UU_MEMORY_ROOT": tmp}):
                for t in tasks:
                    ticket_store.write(t)
                with patch.object(q, "_classifier_stamp_in_flight",
                                  lambda *a, **k: None), patch(
                    "os.path.exists", return_value=False
                ):
                    buf = io.StringIO()
                    with patch("sys.stdout", buf):
                        q.cmd_next(args)
                    return buf.getvalue().strip()

    def test_max_difficulty_flag_filters_by_difficulty(self):
        tasks = [_t(id="T-a", target_difficulty=2)]
        assert self._cmd_next(["--worker", "igor", "--max-difficulty=1"], tasks) == ""

    def test_max_difficulty_flag_returns_eligible_ticket(self):
        tasks = [_t(id="T-a", target_difficulty=1)]
        assert (
            self._cmd_next(["--worker", "igor", "--max-difficulty=1"], tasks) == "T-a"
        )

    def test_no_flag_backward_compat_returns_difficulty_2(self):
        tasks = [_t(id="T-a", target_difficulty=2)]
        assert self._cmd_next(["--worker", "igor"], tasks) == "T-a"

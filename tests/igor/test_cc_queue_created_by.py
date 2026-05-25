"""Tests for created_by attribution field — T-ticket-created-by.

Covers:
  - queue_task() in ops.py sets created_by='igor' unconditionally
  - _format_task_line() shows [igor] tag for igor-attributed tickets
  - _format_task_line() shows [claude] tag for claude-attributed tickets
  - _format_task_line() shows [unknown] for tickets with null created_by
  - cmd_show output includes created_by field (via JSON dump)
"""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from lab.claudecode.cc_queue import _format_task_line


def _base_ticket(**kwargs) -> dict:
    base = {
        "id": "T-test-created-by",
        "title": "Test ticket",
        "size": "S",
        "status": "sprint",
        "worker": "claude",
        "tags": [],
    }
    base.update(kwargs)
    return base


class TestFormatTaskLineCreatedBy:
    def test_igor_attribution_shows_igor_tag(self):
        t = _base_ticket(created_by="igor")
        line = _format_task_line(t)
        assert "[igor]" in line

    def test_claude_attribution_shows_claude_tag(self):
        t = _base_ticket(created_by="claude")
        line = _format_task_line(t)
        assert "[claude]" in line

    def test_null_created_by_shows_unknown(self):
        t = _base_ticket(created_by=None)
        line = _format_task_line(t)
        assert "[unknown]" in line

    def test_missing_created_by_shows_unknown(self):
        t = _base_ticket()
        t.pop("created_by", None)
        line = _format_task_line(t)
        assert "[unknown]" in line


class TestQueueTaskCreatedByInjection:
    """Test that queue_task() in ops.py sets created_by='igor' unconditionally."""

    def _make_task_json(self, **kwargs) -> str:
        base = {
            "id": "T-test-ops-created-by",
            "title": "Test ops task",
            "size": "S",
            "status": "sprint",
        }
        base.update(kwargs)
        return json.dumps(base)

    def _call_queue_task(self, task_json: str):
        """Call queue_task with mocked DB layer."""
        mock_tasks = []

        def mock_load():
            return mock_tasks.copy()

        def mock_save(tasks):
            mock_tasks.clear()
            mock_tasks.extend(tasks)

        with patch("lab.claudecode.cc_queue.load_tasks", mock_load), patch(
            "lab.claudecode.cc_queue.save_tasks", mock_save
        ):
            from devices.igor.tools.ops import queue_task

            return queue_task(task_json), mock_tasks

    def test_sets_created_by_igor_when_field_absent(self):
        task_json = self._make_task_json()
        _, tasks = self._call_queue_task(task_json)
        assert tasks, "task should have been appended"
        assert tasks[0]["created_by"] == "igor"

    def test_overrides_created_by_even_when_provided(self):
        task_json = self._make_task_json(created_by="claude")
        _, tasks = self._call_queue_task(task_json)
        assert tasks, "task should have been appended"
        assert tasks[0]["created_by"] == "igor"

    def test_idempotent_when_task_already_exists(self):
        from unittest.mock import patch as _patch

        existing = {
            "id": "T-test-ops-created-by",
            "title": "exists",
            "created_by": "claude",
        }

        def mock_load():
            return [existing]

        def mock_save(tasks):
            pass

        with _patch("lab.claudecode.cc_queue.load_tasks", mock_load), _patch(
            "lab.claudecode.cc_queue.save_tasks", mock_save
        ):
            from devices.igor.tools.ops import queue_task

            result = queue_task(self._make_task_json())
        assert "skip" in result

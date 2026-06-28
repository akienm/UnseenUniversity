"""
tests/test_deferred_self_task.py — T-igor-deferred-self-tasks

Tests cover:
  - parse_deferred_tasks: extracts DEFERRED_TASK lines, ignores normal text
  - strip_deferred_tasks: removes DEFERRED_TASK lines from reply
  - dispatch_deferred_task: submits job for each supported type
  - push_deferred_result_to_twm: routes only deferred_self_task titles
"""

from __future__ import annotations

import sys
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, call, patch


def _add_repo():
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo()


class TestParseDeferred(unittest.TestCase):

    def _parse(self, text):
        from unseen_university.devices.igor.tools.deferred_self_task import parse_deferred_tasks

        return parse_deferred_tasks(text)

    def test_no_tasks_returns_empty(self):
        result = self._parse("Hello, I have no deferred tasks today.")
        self.assertEqual(result, [])

    def test_single_memory_search(self):
        text = "Some reply.\nDEFERRED_TASK|memory_search|reading list\nMore text."
        tasks = self._parse(text)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["type"], "memory_search")
        self.assertEqual(tasks[0]["payload"], "reading list")

    def test_multiple_tasks(self):
        text = (
            "DEFERRED_TASK|twm_read|\n"
            "DEFERRED_TASK|memory_search|inbox status\n"
            "DEFERRED_TASK|note|check back on this\n"
        )
        tasks = self._parse(text)
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0]["type"], "twm_read")
        self.assertEqual(tasks[1]["type"], "memory_search")
        self.assertEqual(tasks[2]["type"], "note")

    def test_tool_call_with_args(self):
        text = 'DEFERRED_TASK|tool_call|list_unvalidated_memories|{"limit": 5}'
        tasks = self._parse(text)
        self.assertEqual(tasks[0]["type"], "tool_call")
        self.assertIn("list_unvalidated_memories", tasks[0]["payload"])

    def test_ring_read_with_category(self):
        text = "DEFERRED_TASK|ring_read|system_info"
        tasks = self._parse(text)
        self.assertEqual(tasks[0]["type"], "ring_read")
        self.assertEqual(tasks[0]["payload"], "system_info")


class TestStripDeferred(unittest.TestCase):

    def _strip(self, text):
        from unseen_university.devices.igor.tools.deferred_self_task import strip_deferred_tasks

        return strip_deferred_tasks(text)

    def test_strips_deferred_lines(self):
        text = "Here is my answer.\nDEFERRED_TASK|twm_read|\nSee you next turn."
        result = self._strip(text)
        self.assertNotIn("DEFERRED_TASK", result)
        self.assertIn("Here is my answer.", result)
        self.assertIn("See you next turn.", result)

    def test_no_tasks_unchanged(self):
        text = "Normal reply with no tasks."
        self.assertEqual(self._strip(text), text)

    def test_only_deferred_becomes_empty(self):
        text = "DEFERRED_TASK|note|do this"
        result = self._strip(text)
        self.assertEqual(result, "")


class TestDispatchDeferred(unittest.TestCase):

    def _dispatch(self, task_type, payload="", extra_payload=""):
        from unseen_university.devices.igor.tools.deferred_self_task import dispatch_deferred_task

        mock_cortex = MagicMock()
        mock_jm = MagicMock()
        mock_jm.submit_background.return_value = "job-abc123"
        q = deque()

        task = {"type": task_type, "payload": payload + extra_payload, "raw": ""}
        job_id = dispatch_deferred_task(task, mock_cortex, mock_jm, q, thread_id="")
        return job_id, mock_jm

    def test_memory_search_submits_job(self):
        job_id, mock_jm = self._dispatch("memory_search", "reading list")
        mock_jm.submit_background.assert_called_once()
        self.assertEqual(job_id, "job-abc123")

    def test_twm_read_submits_job(self):
        job_id, mock_jm = self._dispatch("twm_read", "")
        mock_jm.submit_background.assert_called_once()

    def test_ring_read_submits_job(self):
        job_id, mock_jm = self._dispatch("ring_read", "system_info")
        mock_jm.submit_background.assert_called_once()

    def test_note_submits_job(self):
        job_id, mock_jm = self._dispatch("note", "remember this")
        mock_jm.submit_background.assert_called_once()

    def test_unsupported_type_returns_none(self):
        job_id, mock_jm = self._dispatch("unknown_type", "payload")
        self.assertIsNone(job_id)
        mock_jm.submit_background.assert_not_called()

    def test_tool_call_submits_job(self):
        job_id, mock_jm = self._dispatch("tool_call", "list_unvalidated_memories|{}")
        mock_jm.submit_background.assert_called_once()


class TestPushDeferredResult(unittest.TestCase):

    def test_deferred_title_pushes_to_twm(self):
        from unseen_university.devices.igor.tools.deferred_self_task import push_deferred_result_to_twm

        mock_cortex = MagicMock()
        push_deferred_result_to_twm(
            mock_cortex,
            "job-123",
            "deferred_self_task:memory_search:query",
            "results here",
        )
        mock_cortex.twm_push.assert_called_once()
        kwargs = mock_cortex.twm_push.call_args[1]
        self.assertEqual(kwargs["source"], "deferred_self_task")
        self.assertIn("DEFERRED_RESULT", kwargs["content_csb"])

    def test_non_deferred_title_skips(self):
        from unseen_university.devices.igor.tools.deferred_self_task import push_deferred_result_to_twm

        mock_cortex = MagicMock()
        push_deferred_result_to_twm(
            mock_cortex, "job-456", "some_other_job:title", "result"
        )
        mock_cortex.twm_push.assert_not_called()


class TestJobFunctions(unittest.TestCase):
    """Unit tests for the job lambdas returned by _make_job_fn."""

    def test_memory_search_fn_returns_hits(self):
        from unseen_university.devices.igor.tools.deferred_self_task import _make_job_fn

        mock_cortex = MagicMock()
        mock_mem = MagicMock()
        mock_mem.memory_type = "FACTUAL"
        mock_mem.narrative = "reading list entry"
        mock_cortex.search.return_value = [mock_mem]

        fn = _make_job_fn("memory_search", "reading list", mock_cortex)
        result = fn()
        self.assertIn("memory_search", result)
        self.assertIn("reading list entry", result)

    def test_memory_search_fn_no_results(self):
        from unseen_university.devices.igor.tools.deferred_self_task import _make_job_fn

        mock_cortex = MagicMock()
        mock_cortex.search.return_value = []
        fn = _make_job_fn("memory_search", "nothing", mock_cortex)
        result = fn()
        self.assertIn("no results", result)

    def test_note_fn_returns_note(self):
        from unseen_university.devices.igor.tools.deferred_self_task import _make_job_fn

        fn = _make_job_fn("note", "check back on reading list", MagicMock())
        result = fn()
        self.assertIn("check back on reading list", result)

    def test_twm_read_fn_empty(self):
        from unseen_university.devices.igor.tools.deferred_self_task import _make_job_fn

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = []
        fn = _make_job_fn("twm_read", "", mock_cortex)
        result = fn()
        self.assertIn("empty", result)

    def test_twm_read_fn_with_items(self):
        from unseen_university.devices.igor.tools.deferred_self_task import _make_job_fn

        mock_cortex = MagicMock()
        mock_cortex.twm_read.return_value = [
            {
                "source": "stdin",
                "urgency": 0.5,
                "salience": 0.6,
                "content_csb": "user hello",
            }
        ]
        fn = _make_job_fn("twm_read", "", mock_cortex)
        result = fn()
        self.assertIn("stdin", result)


if __name__ == "__main__":
    unittest.main()

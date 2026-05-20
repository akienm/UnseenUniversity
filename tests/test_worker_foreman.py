"""
tests/test_worker_foreman.py — T-worker-dispatch-routing.

Covers D-worker-mode-routing-2026-04-21: worker dispatch by ticket metadata.

Tests:
  - cc_queue._infer_worker auto-defaults on add:
      1. Plain ticket (no tags, size=S)        → worker='igor'
      2. HIGH-inertia tagged ticket            → worker='claude'
      3. Explicit worker='claude' respected    (set-worker path, not inferred)
      4. size=XL                               → worker='claude'
      5. description touches brainstem/        → worker='claude'
  - worker_foreman.launch_next_worker dispatch switch:
      6. Top pending worker='igor'  → adopt_next_ticket branch
      7. Top pending worker='claude'→ konsole-spawn branch (not adopt)
      8. Top pending worker missing → konsole-spawn branch (safe default)

End-to-end verification is T-worker-dispatch-validation; this ticket is
plumbing only.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── repo path ─────────────────────────────────────────────────────────────────


def _add_repo_to_path() -> None:
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


_add_repo_to_path()

from lab.claudecode import cc_queue  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# _infer_worker — auto-default routing rule
# ─────────────────────────────────────────────────────────────────────────────


class TestInferWorker(unittest.TestCase):
    """cc_queue._infer_worker: routing rule by tags/size/paths."""

    def test_plain_ticket_defaults_to_igor(self):
        """A plain small ticket with no HIGH signals → igor (cheap tier)."""
        t = {"id": "T-plain", "title": "small fix", "size": "S", "tags": []}
        self.assertEqual(cc_queue._infer_worker(t), "igor")

    def test_medium_ticket_defaults_to_igor(self):
        """M is still below the HIGH-inertia bar → igor."""
        t = {
            "id": "T-medium",
            "title": "add a dashboard column",
            "size": "M",
            "tags": ["Operations"],
        }
        self.assertEqual(cc_queue._infer_worker(t), "igor")

    def test_high_inertia_tag_routes_to_claude(self):
        """Any of HIGH / high-inertia / HIGH-inertia / high_inertia → claude."""
        for tag in ["HIGH", "high-inertia", "HIGH-inertia", "high_inertia"]:
            with self.subTest(tag=tag):
                t = {
                    "id": "T-hi",
                    "title": "touch brainstem",
                    "size": "M",
                    "tags": [tag],
                }
                self.assertEqual(cc_queue._infer_worker(t), "claude")

    def test_xl_size_routes_to_claude(self):
        """XL-sized work stays with CC regardless of tags."""
        t = {"id": "T-xl", "title": "big refactor", "size": "XL", "tags": []}
        self.assertEqual(cc_queue._infer_worker(t), "claude")

    def test_brainstem_path_in_description_routes_to_claude(self):
        """Description referencing brainstem/ → HIGH-inertia path → claude."""
        t = {
            "id": "T-bs",
            "title": "rewire reflexes",
            "description": "Refactor brainstem/reflexes.py to handle foo",
            "size": "M",
            "tags": [],
        }
        self.assertEqual(cc_queue._infer_worker(t), "claude")

    def test_memory_models_path_routes_to_claude(self):
        """memory/models.py in description → claude."""
        t = {
            "id": "T-mm",
            "title": "add memory type",
            "description": "new column on memory/models.py",
            "size": "S",
            "tags": [],
        }
        self.assertEqual(cc_queue._infer_worker(t), "claude")

    def test_reasoners_base_path_routes_to_claude(self):
        """cognition/reasoners/base.py in description → claude."""
        t = {
            "id": "T-rb",
            "title": "tweak base reasoner",
            "body": "Adjust cognition/reasoners/base.py timing.",
            "tags": [],
        }
        self.assertEqual(cc_queue._infer_worker(t), "claude")

    def test_required_files_brainstem_routes_to_claude(self):
        """required_files entries count as path signal too."""
        t = {
            "id": "T-rf",
            "title": "touch reflex",
            "required_files": ["brainstem/reflex_graph.py"],
            "tags": [],
        }
        self.assertEqual(cc_queue._infer_worker(t), "claude")


# ─────────────────────────────────────────────────────────────────────────────
# cmd_add — explicit worker is preserved; missing worker is inferred
# ─────────────────────────────────────────────────────────────────────────────


_VALID_DESC = (
    "**Affected files:** tests\n" "**Test plan:** unit test only — no DB changes."
)


class TestCmdAddWorkerDefault(unittest.TestCase):
    """cmd_add auto-defaults worker when not set; respects explicit worker."""

    def _run_add(self, new_ticket: dict) -> dict:
        """Invoke cmd_add with _load/_save stubbed and return the persisted ticket."""
        saved: dict = {}

        def fake_load():
            return []

        def fake_save(tasks):
            # cmd_add appends to the list then calls _save
            saved["tasks"] = tasks

        with (
            patch.object(cc_queue, "_load", fake_load),
            patch.object(cc_queue, "_save", fake_save),
            patch.object(cc_queue, "_log", lambda _entry: None),
            patch.object(
                cc_queue,
                "os",
                MagicMock(path=MagicMock(exists=lambda _p: False)),
            ),
            patch("lab.claudecode.cc_queue.json.loads", return_value=[new_ticket]),
        ):
            # cmd_add takes a json-file-or-inline-json string; we bypass file
            # existence check (mocked os.path.exists → False) so it goes
            # through json.loads (also mocked) to return our ticket.
            cc_queue.cmd_add(["dummy-inline-json"])

        self.assertIn("tasks", saved, "cmd_add did not call _save")
        self.assertEqual(len(saved["tasks"]), 1, "expected one added ticket")
        return saved["tasks"][0]

    def test_plain_ticket_gets_worker_igor(self):
        """New plain ticket with no worker field → inferred 'igor'."""
        nt = {
            "id": "T-add-plain",
            "title": "small chore",
            "size": "S",
            "tags": [],
            "description": _VALID_DESC,
        }
        persisted = self._run_add(nt)
        self.assertEqual(persisted.get("worker"), "igor")

    def test_high_inertia_tag_gets_worker_claude(self):
        """New HIGH-tagged ticket with no worker field → inferred 'claude'."""
        nt = {
            "id": "T-add-hi",
            "title": "risky edit",
            "size": "M",
            "tags": ["HIGH"],
            "description": _VALID_DESC,
        }
        persisted = self._run_add(nt)
        self.assertEqual(persisted.get("worker"), "claude")

    def test_explicit_worker_claude_preserved(self):
        """Explicit worker='claude' on a small plain ticket is respected."""
        nt = {
            "id": "T-add-explicit-claude",
            "title": "small chore",
            "size": "S",
            "tags": [],
            "worker": "claude",
            "description": _VALID_DESC,
        }
        persisted = self._run_add(nt)
        self.assertEqual(persisted.get("worker"), "claude")

    def test_explicit_worker_igor_preserved_even_with_high_tag(self):
        """Explicit worker='igor' wins even if heuristics would say claude."""
        nt = {
            "id": "T-add-explicit-igor",
            "title": "HIGH but Akien says igor anyway",
            "size": "M",
            "tags": ["HIGH"],
            "worker": "igor",
            "description": _VALID_DESC,
        }
        persisted = self._run_add(nt)
        self.assertEqual(persisted.get("worker"), "igor")


# ─────────────────────────────────────────────────────────────────────────────
# launch_next_worker dispatch switch
# ─────────────────────────────────────────────────────────────────────────────


class TestLaunchNextWorkerDispatch(unittest.TestCase):
    """launch_next_worker routes by top pending ticket's worker field."""

    def _run_launch(self, tasks: list) -> tuple[str, MagicMock, MagicMock]:
        """Invoke launch_next_worker with dependencies mocked.

        Returns (result, mock_adopt, mock_popen) for inspection.
        """
        from wild_igor.igor.tools import worker_foreman as wf

        mock_adopt = MagicMock(return_value="adopted T-xyz: mocked")
        mock_popen = MagicMock()
        # Popen instance: wait() returns None; returncode 0; stdout readable
        popen_instance = MagicMock()
        popen_instance.wait.return_value = None
        popen_instance.returncode = 0
        popen_instance.stdout.read.return_value = b"launched ok"
        popen_instance.stderr.read.return_value = b""
        popen_instance.pid = 12345
        mock_popen.return_value = popen_instance

        # Patch Cortex so the active-goal DB check inside launch_next_worker
        # doesn't make these routing tests sensitive to live DB state.
        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = []
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        with (
            patch.object(wf, "_load_queue", return_value=tasks),
            patch.object(wf, "adopt_next_ticket", mock_adopt),
            patch.object(wf.subprocess, "Popen", mock_popen),
            patch(_CORTEX_PATH, return_value=mock_cortex),
            patch(_MT_PATH, mock_mt),
        ):
            result = wf.launch_next_worker()

        return result, mock_adopt, mock_popen

    def test_worker_igor_routes_to_adopt(self):
        """Top pending worker='igor' → adopt_next_ticket is called, no konsole."""
        tasks = [
            {
                "id": "T-igor-1",
                "title": "small chore",
                "status": "sprint",
                "priority": 5,
                "worker": "igor",
                "tags": [],
            }
        ]
        result, mock_adopt, mock_popen = self._run_launch(tasks)

        mock_adopt.assert_called_once_with()
        mock_popen.assert_not_called()
        self.assertIn("adopted", result)

    def test_worker_claude_routes_to_konsole(self):
        """Top pending worker='claude' → konsole-spawn path, not adopt."""
        tasks = [
            {
                "id": "T-claude-1",
                "title": "HIGH work",
                "status": "sprint",
                "priority": 5,
                "worker": "claude",
                "tags": ["HIGH"],
            }
        ]
        result, mock_adopt, mock_popen = self._run_launch(tasks)

        mock_adopt.assert_not_called()
        mock_popen.assert_called_once()
        # First positional arg of Popen is the command list
        cmd = mock_popen.call_args[0][0]
        self.assertIn("worker-launch", cmd)
        self.assertIn("T-claude-1", cmd)

    def test_missing_worker_defaults_to_konsole(self):
        """Top pending with no worker field → safe default = claude → konsole."""
        tasks = [
            {
                "id": "T-nw-1",
                "title": "legacy ticket",
                "status": "sprint",
                "priority": 5,
                "tags": [],
                # no 'worker' field at all
            }
        ]
        result, mock_adopt, mock_popen = self._run_launch(tasks)

        mock_adopt.assert_not_called()
        mock_popen.assert_called_once()

    def test_igor_chosen_over_later_claude(self):
        """Worker field of the *top-priority* pending ticket drives dispatch."""
        tasks = [
            {
                "id": "T-lower-claude",
                "title": "low priority claude work",
                "status": "sprint",
                "priority": 99,
                "worker": "claude",
                "tags": [],
            },
            {
                "id": "T-top-igor",
                "title": "high priority igor work",
                "status": "sprint",
                "priority": 1,
                "worker": "igor",
                "tags": [],
            },
        ]
        result, mock_adopt, mock_popen = self._run_launch(tasks)

        mock_adopt.assert_called_once()
        mock_popen.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# adopt_next_ticket — IGOR_STRICT_CLAIM_MODEL env var
# ─────────────────────────────────────────────────────────────────────────────


_CORTEX_PATH = "wild_igor.igor.memory.cortex.Cortex"
_MT_PATH = "wild_igor.igor.memory.models.MemoryType"
_OPS_GOAL_ADOPT_PATH = "wild_igor.igor.tools.ops.goal_adopt"


class TestAdoptNextTicketStrictFlag(unittest.TestCase):
    """adopt_next_ticket sets IGOR_STRICT_CLAIM_MODEL=1 before pe_chain runs."""

    def test_env_flag_set_before_pe_chain(self):
        """IGOR_STRICT_CLAIM_MODEL=1 is in os.environ when pe_chain is called."""
        from wild_igor.igor.tools import worker_foreman as wf

        env_at_chain_call: dict = {}

        def capture_env_and_return(*_a, **_kw):
            env_at_chain_call.update(os.environ)
            return "chain done"

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = []  # no active goals

        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        pending = [
            {
                "id": "T-strict-flag-test",
                "title": "env test ticket",
                "status": "sprint",
                "priority": 5,
                "worker": "igor",
                "tags": [],
            }
        ]

        mock_pe_tool = MagicMock()
        mock_pe_tool.fn = capture_env_and_return

        mock_next_result = MagicMock()
        mock_next_result.stdout = "T-strict-flag-test\n"

        original_flag = os.environ.pop("IGOR_STRICT_CLAIM_MODEL", None)
        try:
            with (
                patch("subprocess.run", return_value=mock_next_result),
                patch(_CORTEX_PATH, return_value=mock_cortex),
                patch(_MT_PATH, mock_mt),
                patch(_OPS_GOAL_ADOPT_PATH, return_value="adopted"),
                patch.object(wf.registry, "get", return_value=mock_pe_tool),
            ):
                wf.adopt_next_ticket()
        finally:
            if original_flag is not None:
                os.environ["IGOR_STRICT_CLAIM_MODEL"] = original_flag
            else:
                os.environ.pop("IGOR_STRICT_CLAIM_MODEL", None)

        self.assertEqual(
            env_at_chain_call.get("IGOR_STRICT_CLAIM_MODEL"),
            "1",
            "IGOR_STRICT_CLAIM_MODEL was not '1' when pe_chain was invoked",
        )

    def test_empty_cmd_next_returns_no_eligible(self):
        """adopt_next_ticket returns early when cmd_next returns empty."""
        from wild_igor.igor.tools import worker_foreman as wf

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = []

        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        mock_empty_result = MagicMock()
        mock_empty_result.stdout = ""

        with (
            patch("subprocess.run", return_value=mock_empty_result),
            patch(_CORTEX_PATH, return_value=mock_cortex),
            patch(_MT_PATH, mock_mt),
        ):
            result = wf.adopt_next_ticket()

        self.assertIn("no eligible", result)

    def test_cmd_next_called_with_max_difficulty_1(self):
        """adopt_next_ticket passes --max-difficulty=1 to cmd_next."""
        from wild_igor.igor.tools import worker_foreman as wf

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = []

        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        mock_empty_result = MagicMock()
        mock_empty_result.stdout = ""

        with (
            patch("subprocess.run", return_value=mock_empty_result) as mock_run,
            patch(_CORTEX_PATH, return_value=mock_cortex),
            patch(_MT_PATH, mock_mt),
        ):
            wf.adopt_next_ticket()

        call_args = mock_run.call_args[0][0]
        self.assertIn("--max-difficulty=1", call_args)
        self.assertIn("igor", call_args)


if __name__ == "__main__":
    unittest.main()

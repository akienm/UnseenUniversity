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
            # _scraps_validate calls ScrapsDevice → InferenceDevice → live
            # OpenRouter API call. These tests cover worker routing, not ticket
            # validation, so mock it out to keep tests fast and offline-safe.
            patch.object(cc_queue, "_scraps_validate", return_value=True),
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
        from devices.igor.tools import worker_foreman as wf

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

    def test_worker_igor_returns_dispatch_hint(self):
        """Top pending worker='igor' → dispatch hint returned, no adopt, no konsole."""
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

        mock_adopt.assert_not_called()
        mock_popen.assert_not_called()
        self.assertIn("T-igor-1", result)
        self.assertIn("dispatch", result.lower())

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
        """Worker field of the *top-priority* pending ticket drives dispatch hint."""
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

        mock_adopt.assert_not_called()
        mock_popen.assert_not_called()
        self.assertIn("T-top-igor", result)
        self.assertIn("dispatch", result.lower())


# ─────────────────────────────────────────────────────────────────────────────
# adopt_next_ticket — pe_chain dispatch
# ─────────────────────────────────────────────────────────────────────────────


_CORTEX_PATH = "devices.igor.memory.cortex.Cortex"
_MT_PATH = "devices.igor.memory.models.MemoryType"
_OPS_GOAL_ADOPT_PATH = "devices.igor.tools.ops.goal_adopt"


class TestAdoptNextTicketStrictFlag(unittest.TestCase):
    """adopt_next_ticket raises LegacyDirectClaimError unconditionally."""

    def test_adopt_next_ticket_raises_legacy_error(self):
        """adopt_next_ticket always raises — autonomous pickup is removed."""
        from lab.claudecode.cc_queue import LegacyDirectClaimError
        from devices.igor.tools import worker_foreman as wf

        with self.assertRaises(LegacyDirectClaimError) as ctx:
            wf.adopt_next_ticket()
        self.assertIn("dispatch", str(ctx.exception).lower())

    def test_launch_next_worker_igor_ticket_returns_dispatch_hint(self):
        """launch_next_worker with worker=igor returns dispatch hint, not adopted."""
        tasks = [
            {
                "id": "T-igor-dispatch",
                "title": "needs dispatch",
                "status": "sprint",
                "priority": 5,
                "worker": "igor",
                "tags": [],
            }
        ]
        from devices.igor.tools import worker_foreman as wf

        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = []

        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        with (
            patch.object(wf, "_load_queue", return_value=tasks),
            patch("devices.igor.tools.worker_foreman.subprocess.Popen", MagicMock()),
            patch(_CORTEX_PATH, return_value=mock_cortex),
            patch(_MT_PATH, mock_mt),
        ):
            result = wf.launch_next_worker()

        self.assertIn("T-igor-dispatch", result)
        self.assertIn("dispatch", result.lower())


# ─────────────────────────────────────────────────────────────────────────────
# daemon-dead stale-reset race (T-worker-foreman-save-tasks-race-stale-bul)
# ─────────────────────────────────────────────────────────────────────────────


class TestDaemonDeadRaceCondition(unittest.TestCase):
    """Daemon-dead branch uses targeted reset, not bulk save_tasks.

    Race: ticket in_progress at load time; setstatus/close cancels it
    concurrently in DB; reset_stale_in_progress returns False → ticket
    stays terminal, not resurrected as sprint.
    """

    def _run_foreman_with_stale_in_progress(
        self, reset_returns: bool
    ) -> tuple[str, MagicMock]:
        """Run launch_next_worker with one in_progress ticket (claude worker).

        daemon_pid exists but is dead; reset_stale_in_progress is mocked.
        Returns (result, mock_reset).
        """
        from devices.igor.tools import worker_foreman as wf

        task = {
            "id": "T-stale-1",
            "title": "was in progress",
            "status": "in_progress",
            "priority": 5,
            "worker": "claude",
            "claimed_at": "2026-05-24T00:00:00+00:00",
            "tags": [],
        }
        tasks = [task]

        mock_reset = MagicMock(return_value=reset_returns)
        mock_cortex = MagicMock()
        mock_cortex.get_by_type.return_value = []
        mock_mt = MagicMock()
        mock_mt.GOAL = "GOAL"

        fake_pids = {"daemon": {"konsole_pid": 99999}}
        fake_pids_path = MagicMock()
        fake_pids_path.exists.return_value = True
        fake_pids_path.read_text.return_value = json.dumps(fake_pids)

        mock_popen = MagicMock()
        popen_instance = MagicMock()
        popen_instance.wait.return_value = 0
        popen_instance.returncode = 0
        popen_instance.stdout.read.return_value = b"worker-launch mocked"
        mock_popen.return_value = popen_instance

        with (
            patch.object(wf, "_load_queue", return_value=tasks),
            patch.object(wf, "_WORKER_PIDS_PATH", fake_pids_path),
            patch.object(wf, "_pid_alive", return_value=False),
            patch(
                "devices.igor.tools.worker_foreman._cc_queue",
                create=True,
            ),
            patch(
                "lab.claudecode.cc_queue.reset_stale_in_progress",
                mock_reset,
            ),
            patch(_CORTEX_PATH, return_value=mock_cortex),
            patch(_MT_PATH, mock_mt),
            patch.object(wf.subprocess, "Popen", mock_popen),
        ):
            # Patch reset and set_status_in_progress on the live module
            # so the local `from lab.claudecode import cc_queue` inside the
            # function body sees both mocks.
            import lab.claudecode.cc_queue as ccq

            orig_reset = ccq.reset_stale_in_progress
            orig_set = ccq.set_status_in_progress
            ccq.reset_stale_in_progress = mock_reset
            ccq.set_status_in_progress = MagicMock()
            try:
                result = wf.launch_next_worker()
            finally:
                ccq.reset_stale_in_progress = orig_reset
                ccq.set_status_in_progress = orig_set

        return result, mock_reset, task

    def test_concurrent_cancel_leaves_ticket_terminal(self):
        """reset_stale_in_progress returns False → in-memory status stays in_progress."""
        result, mock_reset, task = self._run_foreman_with_stale_in_progress(
            reset_returns=False
        )
        mock_reset.assert_called_once_with("T-stale-1")
        # Ticket must NOT have been flipped to sprint in memory
        self.assertEqual(task["status"], "in_progress")
        # Queue should report clear (no sprint-ready tickets)
        self.assertIn("clear", result)

    def test_genuinely_stale_ticket_is_reset(self):
        """reset_stale_in_progress returns True → in-memory status becomes sprint."""
        result, mock_reset, task = self._run_foreman_with_stale_in_progress(
            reset_returns=True
        )
        mock_reset.assert_called_once_with("T-stale-1")
        # Ticket should have been reset in memory
        self.assertEqual(task["status"], "sprint")
        self.assertNotIn("claimed_at", task)


if __name__ == "__main__":
    unittest.main()

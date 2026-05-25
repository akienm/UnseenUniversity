"""
tests/test_queue_consumer.py — Unit tests for T-goal-queue-consumer.

Covers read_queue_top() and adopt_top_queue_ticket() from ops.py.

Tests:
  read_queue_top:
    - Returns top pending ticket sorted by priority
    - Returns 'no pending tickets' when no pending+igor tickets exist
    - Returns 'no pending tickets' on empty queue file

  adopt_top_queue_ticket:
    - Raises LegacyDirectClaimError unconditionally (autonomous drain removed)
    - D-igor-queue-encapsulation: Igor receives tickets via CC dispatch only

No Postgres, no filesystem I/O — Cortex and cc_queue.load_tasks are mocked.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── repo path ──────────────────────────────────────────────────────────────────

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# ── patch targets ─────────────────────────────────────────────────────────────

_CORTEX_PATH = "devices.igor.memory.cortex.Cortex"
_MT_PATH = "devices.igor.memory.models.MemoryType"
# read_queue_top and adopt_top_queue_ticket now use cc_queue.load_tasks (Postgres)
_LOAD_TASKS_PATH = "lab.claudecode.cc_queue.load_tasks"

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_ticket(
    id: str, priority: int = 5, status: str = "sprint", worker: str = "igor"
) -> dict:
    return {
        "id": id,
        "title": f"Do {id}",
        "status": status,
        "priority": priority,
        "worker": worker,
    }


def _make_goal(active: bool = True, source: str = "work ticket T-alpha") -> MagicMock:
    g = MagicMock()
    g.id = "GOAL_20260401120000000001"
    g.narrative = f"ACTIVE GOAL: {source}"
    g.metadata = {
        "goal_active": active,
        "source_message": source,
        "adopted_at": "2026-04-01T12:00:00Z",
        "failure_count": 0,
    }
    return g


# ── read_queue_top tests ───────────────────────────────────────────────────────


class TestReadQueueTop(unittest.TestCase):

    def _call(self, tickets: list[dict]) -> str:
        from devices.igor.tools.ops import read_queue_top

        with patch(_LOAD_TASKS_PATH, return_value=tickets):
            return read_queue_top()

    def test_returns_top_pending_ticket(self):
        """Returns the pending claude-worker ticket with lowest priority number."""
        tickets = [
            _make_ticket("T-beta", priority=2),
            _make_ticket("T-alpha", priority=1),
        ]
        result = self._call(tickets)
        self.assertIn("T-alpha", result)
        self.assertIn("top ticket", result)

    def test_priority_sort_ascending(self):
        """Lower priority number wins."""
        tickets = [
            _make_ticket("T-low", priority=10),
            _make_ticket("T-high", priority=1),
        ]
        result = self._call(tickets)
        self.assertIn("T-high", result)
        self.assertNotIn("T-low", result)

    def test_no_pending_tickets_empty_queue(self):
        """Returns 'no pending tickets' when queue file is empty list."""
        result = self._call([])
        self.assertEqual(result, "no sprint tickets")

    def test_no_pending_tickets_all_done(self):
        """Returns 'no pending tickets' when all tickets are done."""
        tickets = [_make_ticket("T-done", status="done")]
        result = self._call(tickets)
        self.assertEqual(result, "no sprint tickets")

    def test_no_pending_tickets_wrong_worker(self):
        """Returns 'no pending tickets' when pending tickets belong to a different worker."""
        tickets = [_make_ticket("T-foreman", worker="foreman")]
        result = self._call(tickets)
        self.assertEqual(result, "no sprint tickets")

    def test_filters_non_igor_worker(self):
        """Only returns tickets with worker=='igor'."""
        tickets = [
            _make_ticket("T-foreman", priority=1, worker="foreman"),
            _make_ticket("T-igor", priority=2, worker="igor"),
        ]
        result = self._call(tickets)
        self.assertIn("T-igor", result)

    def test_includes_title_in_result(self):
        """Result contains the ticket title."""
        tickets = [_make_ticket("T-work", priority=1)]
        result = self._call(tickets)
        self.assertIn("Do T-work", result)

    def test_load_error_returns_error_string(self):
        """Returns an error string if cc_queue.load_tasks raises."""
        from devices.igor.tools.ops import read_queue_top

        with patch(_LOAD_TASKS_PATH, side_effect=Exception("db connection failed")):
            result = read_queue_top()
        self.assertIn("[read_queue_top] error", result)


# ── adopt_top_queue_ticket tests ───────────────────────────────────────────────


class TestAdoptTopQueueTicket(unittest.TestCase):
    """adopt_top_queue_ticket raises LegacyDirectClaimError — autonomous drain removed.

    D-igor-queue-encapsulation: Igor receives tickets only via CC dispatch.
    """

    def test_raises_legacy_direct_claim_error(self):
        """adopt_top_queue_ticket always raises — PROC_QUEUE_DRAIN autonomous pickup is gone."""
        from lab.claudecode.cc_queue import LegacyDirectClaimError
        from devices.igor.tools.ops import adopt_top_queue_ticket

        with self.assertRaises(LegacyDirectClaimError) as ctx:
            adopt_top_queue_ticket()
        self.assertIn("dispatch", str(ctx.exception).lower())


# ── registry registration check ───────────────────────────────────────────────


class TestRegistryRegistration(unittest.TestCase):

    def test_read_queue_top_registered(self):
        """read_queue_top is present in the tool registry."""
        from lab.utility_closet.registry import registry
        import devices.igor.tools.ops  # noqa: F401 — triggers registration

        tool = registry.get("read_queue_top")
        self.assertIsNotNone(tool, "read_queue_top not found in registry")
        self.assertEqual(tool.fn.__qualname__, "read_queue_top")

    def test_adopt_top_queue_ticket_registered(self):
        """adopt_top_queue_ticket is present in the tool registry with no required args."""
        from lab.utility_closet.registry import registry
        import devices.igor.tools.ops  # noqa: F401

        tool = registry.get("adopt_top_queue_ticket")
        self.assertIsNotNone(tool, "adopt_top_queue_ticket not found in registry")
        # Zero required args — scheduler calls tool.fn() with no arguments
        required = tool.parameters.get("required", [])
        self.assertEqual(
            required, [], "adopt_top_queue_ticket must have no required args"
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for dispatch-IS-assignment model (T-dispatch-is-assignment).

cmd_next atomically marks the ticket in_progress at dispatch.
Direct CC sprints use setstatus in_progress (no claim).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import devlab.claudecode.cc_queue as q


def _task(**kw):
    base = {
        "id": "T-test",
        "title": "test ticket",
        "status": "sprint",
        "worker": "claude",
        "gate": None,
        "priority": 0.5,
        "size": "S",
        "tags": [],
        "decision_id": None,
        "description": "test",
        "result": None,
        "dispatched_at": None,
        "target_difficulty": 1,
    }
    base.update(kw)
    return base


class TestNextTicketIdDoesNotMutate:
    """next_ticket_id_for_worker is read-only — mutation happens in cmd_next."""

    def test_returns_id_without_marking_in_progress(self):
        tasks = [_task(id="T-alpha", worker="claude", status="sprint")]
        with patch.object(q, "_load", return_value=tasks):
            result = q.next_ticket_id_for_worker("claude")
        assert result == "T-alpha"
        # next_ticket_id_for_worker must NOT mutate status
        assert tasks[0]["status"] == "sprint"


class TestCmdNextMarksInProgress:
    """cmd_next atomically marks the winning ticket in_progress (dispatch IS assignment)."""

    def test_cmd_next_marks_ticket_in_progress(self):
        """After cmd_next returns a ticket, it must be in_progress in the DB."""
        import os
        import psycopg2

        pg = os.environ.get(
            "UU_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        try:
            conn = psycopg2.connect(pg)
        except Exception:
            import pytest

            pytest.skip("Postgres not available")

        import psycopg2.extras, json

        ticket_id = "T-dispatch-test-tmp"
        try:
            # Insert a sprint ticket for this test
            cur = conn.cursor()
            md = psycopg2.extras.Json(
                {
                    "id": ticket_id,
                    "title": "dispatch test tmp",
                    "status": "sprint",
                    "worker": "claude",
                    "gate": None,
                    "priority": 0.01,
                    "size": "S",
                    "tags": ["test_data"],
                    "decision_id": None,
                    "description": "temporary dispatch test ticket",
                    "result": None,
                    "dispatched_at": None,
                    "target_difficulty": 1,
                    "kind": "ticket",
                    "test_data": "true",
                }
            )
            cur.execute(
                "INSERT INTO clan.memories (id, memory_type, parent_id, metadata) "
                "VALUES (%s, 'PROCEDURAL', %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET metadata = EXCLUDED.metadata",
                (ticket_id, q.TICKETS_ROOT_ID, md),
            )
            conn.commit()

            # Call cmd_next — pin next_ticket_id_for_worker to our test ticket so
            # other higher-priority sprint tickets in the live queue don't win the
            # race.  The mock isolates the selection step; we test only the atomic
            # in_progress marking that cmd_next performs after selection.
            import io

            buf = io.StringIO()
            with patch("sys.stdout", buf), patch.object(
                q, "next_ticket_id_for_worker", return_value=ticket_id
            ):
                q.cmd_next(["--worker", "claude", "--max-difficulty=1"])
            output = buf.getvalue().strip()

            # Verify the ticket is now in_progress in the DB
            cur.execute(
                "SELECT metadata->>'status' FROM clan.memories WHERE id = %s",
                (ticket_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "in_progress", f"Expected in_progress, got {row[0]}"
            assert output == ticket_id

        finally:
            cur.execute("DELETE FROM clan.memories WHERE id = %s", (ticket_id,))
            conn.commit()
            conn.close()

    def test_cmd_next_returns_nothing_when_queue_empty(self):
        """cmd_next prints nothing and exits cleanly when no eligible ticket."""
        with patch.object(q, "next_ticket_id_for_worker", return_value=None):
            import io

            buf = io.StringIO()
            with patch("sys.stdout", buf):
                q.cmd_next(["--worker", "claude"])
            assert buf.getvalue().strip() == ""

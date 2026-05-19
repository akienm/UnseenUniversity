"""Tests for cc_queue.py cmd_claim worker check.

# author-model: opus

Original: pinned the cmd_claim source-line shape to enforce that pe_chain
(no --as flag) cannot claim non-igor tickets — load-bearing for the
cert_worker_freeze design (T-flip-igor-worker-tickets-during-cert).

Updated 2026-05-03 (T-cc-queue-claim-as-flag): cmd_claim now accepts an
optional `--as <worker>` flag. Default remains 'igor' so pe_chain calls
without the flag preserve the cert_worker_freeze gate. CC manual claims
pass `--as claude` to claim claude-worker tickets explicitly.

The four behavior cases tested:
  (a) worker=igor, no flag    → success  (legacy pe_chain path)
  (b) worker=claude, no flag  → reject   (cert freeze still blocks Igor)
  (c) worker=claude, --as claude → success (new CC manual path)
  (d) worker=igor, --as claude   → reject (cross-worker claim blocked)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import importlib.util as _ilu

_spec = _ilu.find_spec("lab.claudecode.cc_queue")
CC_QUEUE = (
    Path(_spec.origin)
    if (_spec and _spec.origin)
    else REPO / "lab" / "claudecode" / "cc_queue.py"
)
del _ilu, _spec


def _seeded_db_url() -> str | None:
    """Return the test DB URL if one is set; tests skip otherwise."""
    return os.environ.get("IGOR_HOME_DB_URL")


def _seed_ticket(ticket_id: str, worker: str | None) -> None:
    """Insert a pending ticket directly into clan.memories with the given worker."""
    import psycopg2

    conn = psycopg2.connect(_seeded_db_url())
    try:
        cur = conn.cursor()
        # cc_queue.py reads tickets from clan.memories where parent_id = TICKETS_ROOT_ID.
        # Import the constant rather than hardcoding it to stay in sync.
        sys.path.insert(0, str(REPO))
        from lab.claudecode.cc_queue import TICKETS_ROOT_ID

        metadata = {
            "id": ticket_id,
            "title": f"Test ticket {ticket_id}",
            "size": "S",
            "status": "pending",
            "worker": worker,
            "tags": ["test"],
            "kind": "ticket",
            # Pre-stamp so cmd_claim skips Scraps (worker-routing tests don't
            # care about Scraps; seed tickets have no description to validate).
            "scraps_validated": "2026-01-01T00:00:00+00:00",
        }
        cur.execute(
            "DELETE FROM clan.memories WHERE id = %s",
            (ticket_id,),
        )
        cur.execute(
            """
            INSERT INTO clan.memories
              (id, narrative, memory_type, parent_id, metadata, timestamp,
               source, scope, confidence, updated_at)
            VALUES (%s, %s, 'FACTUAL', %s, %s::jsonb, NOW(), 'cc_queue_test',
                    'class', 1.0, NOW())
            """,
            (ticket_id, metadata["title"], TICKETS_ROOT_ID, json.dumps(metadata)),
        )
        conn.commit()
    finally:
        conn.close()


def _cleanup_ticket(ticket_id: str) -> None:
    import psycopg2

    conn = psycopg2.connect(_seeded_db_url())
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM clan.memories WHERE id = %s", (ticket_id,))
        conn.commit()
    finally:
        conn.close()


def _run_claim(*args: str) -> subprocess.CompletedProcess:
    """Invoke cc_queue.py claim against the live (test-schema) DB."""
    return subprocess.run(
        [sys.executable, str(CC_QUEUE), "claim", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


@pytest.fixture
def db_required():
    if not _seeded_db_url():
        pytest.skip("IGOR_HOME_DB_URL not set — claim-worker behavior tests skipped")


class TestCmdClaimWorkerCheck:
    def test_igor_worker_no_flag_succeeds(self, db_required):
        """Legacy pe_chain path — worker=igor + no --as flag → claim succeeds."""
        tid = "T-test-claim-igor-noflag"
        _seed_ticket(tid, "igor")
        try:
            r = _run_claim(tid)
            assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
            assert "Claimed" in r.stdout
        finally:
            _cleanup_ticket(tid)

    def test_claude_worker_no_flag_rejects(self, db_required):
        """Cert-freeze gate — worker=claude + no --as flag → reject (default as=igor)."""
        tid = "T-test-claim-claude-noflag"
        _seed_ticket(tid, "claude")
        try:
            r = _run_claim(tid)
            assert r.returncode != 0
            assert "worker mismatch" in r.stdout or "not pending" in r.stdout
        finally:
            _cleanup_ticket(tid)

    def test_claude_worker_as_claude_succeeds(self, db_required):
        """CC manual path — worker=claude + --as claude → claim succeeds."""
        tid = "T-test-claim-claude-asclaude"
        _seed_ticket(tid, "claude")
        try:
            r = _run_claim(tid, "--as", "claude")
            assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
            assert "Claimed" in r.stdout
            assert "as claude" in r.stdout
        finally:
            _cleanup_ticket(tid)

    def test_igor_worker_as_claude_rejects(self, db_required):
        """Cross-worker claim — worker=igor + --as claude → reject."""
        tid = "T-test-claim-igor-asclaude"
        _seed_ticket(tid, "igor")
        try:
            r = _run_claim(tid, "--as", "claude")
            assert r.returncode != 0
            assert "worker mismatch" in r.stdout or "not pending" in r.stdout
        finally:
            _cleanup_ticket(tid)

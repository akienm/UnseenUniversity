"""Queue device — work ticket queue served as a rack device.

All consumers (CC, Igor, Librarian) call queue_next(worker) → ticket | None
via MCP instead of calling cc_queue.py directly.

Backend: clan.memories WHERE parent_id='TICKETS_ROOT' in UU_HOME_DB_URL.
This device is stateless — all durable state lives in Postgres.

There is no claim operation. queue_next() is atomic: it reads the next
eligible ticket and marks it in_progress in a single serializable transaction.
Any code that calls queue_claim() raises LegacyDirectClaimError.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unseen_university.device import BaseDevice, INTERFACE_VERSION

_START_TIME = time.time()

TICKETS_ROOT_ID = "TICKETS_ROOT"
GATE_FILE = Path(
    os.environ.get(
        "QUEUE_GATE_FILE", Path.home() / ".unseen_university/cc_channel/queue_gate.json"
    )
)


class LegacyDirectClaimError(Exception):
    """Raised when any code tries to call the removed claim operation.

    Claiming is removed. The only way to receive a ticket is via queue_next(),
    which is atomic (returns ticket + marks in_progress in one transaction).
    """


def _db_url() -> str:
    url = os.environ.get("UU_HOME_DB_URL", "")
    if not url:
        raise RuntimeError(
            "UU_HOME_DB_URL not set — queue device cannot connect to ticket storage"
        )
    return url


def _db_conn():
    import psycopg2

    return psycopg2.connect(_db_url())


def _gate_tripped() -> bool:
    """Return True when the queue gate circuit-breaker is active."""
    if not GATE_FILE.exists():
        return False
    try:
        data = json.loads(GATE_FILE.read_text())
        return bool(data.get("tripped"))
    except Exception:
        return False


def _priority_key(t: dict) -> float:
    """Lower return value = higher priority."""
    p = t.get("priority", 99)
    try:
        v = float(str(p).lstrip("pP"))
        return -v if v <= 1.0 else v
    except (ValueError, TypeError):
        return 99.0


class QueueDevice(BaseDevice):
    """Rack device that serves the work ticket queue.

    MCP tools exposed:
        queue_next(worker)                 — atomic get-or-none, marks in_progress
        queue_peek(worker)                 — read-only, no side effects
        queue_show(ticket_id)              — fetch one ticket by ID
        queue_list(worker, status)         — list tickets matching filters
    """

    DEVICE_ID = "queue"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(device_id=self.DEVICE_ID, **kwargs)
        self._startup_errors: list[str] = []
        # Verify connection at startup (soft — device stays live on failure)
        try:
            conn = _db_conn()
            conn.close()
        except Exception as exc:
            self._startup_errors.append(f"DB connect failed at startup: {exc}")
            self.warning(f"startup: DB not reachable — {exc}")

    # ── Queue operations ──────────────────────────────────────────────────────

    def queue_next(self, worker: str) -> dict | None:
        """Atomically return the next eligible ticket and mark it in_progress.

        Returns None when the queue is empty, the gate is tripped, or no ticket
        matches the worker filter. The ticket is marked in_progress before return
        so no other caller can race for the same ticket.
        """
        if _gate_tripped():
            self.info(f"queue_next: gate tripped — returning None (worker={worker!r})")
            return None

        conn = _db_conn()
        try:
            with conn:
                cur = conn.cursor()
                # Load all sprint tickets for this worker with no gate
                cur.execute(
                    "SELECT metadata FROM clan.memories WHERE parent_id = %s",
                    (TICKETS_ROOT_ID,),
                )
                rows = cur.fetchall()

            candidates = []
            for (md,) in rows:
                if not md:
                    continue
                t = dict(md)
                t.pop("kind", None)
                if (
                    t.get("status") == "sprint"
                    and not t.get("gate")
                    and t.get("worker") == worker
                ):
                    candidates.append(t)

            if not candidates:
                return None

            best = min(candidates, key=_priority_key)
            ticket_id = best["id"]

            # Atomic in_progress mark with SELECT FOR UPDATE
            with conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT metadata FROM clan.memories WHERE id = %s FOR UPDATE",
                    (ticket_id,),
                )
                row = cur.fetchone()
                if not row or not row[0]:
                    return None
                t = dict(row[0])
                if t.get("status") != "sprint":
                    # Another caller claimed it between our read and the lock
                    return None
                now = datetime.now(timezone.utc).isoformat()
                t["status"] = "in_progress"
                t["dispatched_at"] = now
                import psycopg2.extras

                cur.execute(
                    "UPDATE clan.memories SET metadata = %s WHERE id = %s",
                    (psycopg2.extras.Json(t), ticket_id),
                )

            t.pop("kind", None)
            self.trace_record("queue_next", {"worker": worker, "ticket_id": ticket_id})
            self.info(f"queue_next: dispatched {ticket_id!r} to worker={worker!r}")
            return t
        finally:
            conn.close()

    def queue_peek(self, worker: str) -> dict | None:
        """Return the next eligible ticket without marking it in_progress.

        Read-only. Use this when you want to preview what would be dispatched
        without committing to working it.
        """
        if _gate_tripped():
            return None

        conn = _db_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT metadata FROM clan.memories WHERE parent_id = %s",
                (TICKETS_ROOT_ID,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        candidates = []
        for (md,) in rows:
            if not md:
                continue
            t = dict(md)
            t.pop("kind", None)
            if (
                t.get("status") == "sprint"
                and not t.get("gate")
                and t.get("worker") == worker
            ):
                candidates.append(t)

        if not candidates:
            return None
        return min(candidates, key=_priority_key)

    def queue_show(self, ticket_id: str) -> dict | None:
        """Return a single ticket by ID, or None if not found."""
        conn = _db_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT metadata FROM clan.memories WHERE id = %s AND parent_id = %s",
                (ticket_id, TICKETS_ROOT_ID),
            )
            row = cur.fetchone()
        finally:
            conn.close()

        if not row or not row[0]:
            return None
        t = dict(row[0])
        t.pop("kind", None)
        return t

    def queue_list(
        self, worker: str | None = None, status: str = "sprint"
    ) -> list[dict]:
        """Return all tickets matching worker and status filters.

        worker=None returns tickets for all workers.
        status defaults to 'sprint' (ready-to-work tickets).
        """
        conn = _db_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT metadata FROM clan.memories WHERE parent_id = %s",
                (TICKETS_ROOT_ID,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        results = []
        for (md,) in rows:
            if not md:
                continue
            t = dict(md)
            t.pop("kind", None)
            if t.get("status") != status:
                continue
            if worker is not None and t.get("worker") != worker:
                continue
            results.append(t)

        results.sort(key=_priority_key)
        return results

    @staticmethod
    def queue_claim(*args, **kwargs):
        """queue_claim is removed. Use queue_next() instead.

        queue_next() is atomic — it returns the ticket AND marks it in_progress
        in one transaction. There is no separate claim step.
        """
        raise LegacyDirectClaimError(
            "queue_claim is removed. Use queue_next(worker=...) instead. "
            "queue_next() atomically returns a ticket and marks it in_progress."
        )

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Queue",
            "version": "1.0.0",
            "purpose": "Work ticket queue — atomic queue_next, no claiming",
        }

    def requirements(self) -> dict:
        return {"deps": ["psycopg2", "UU_HOME_DB_URL"]}

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": False,
            "emitted_keywords": [],
            "mcp_tools": ["queue_next", "queue_peek", "queue_show", "queue_list"],
        }

    def comms(self) -> dict:
        return {
            "address": "comms://queue",
            "mode": "read_only",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            conn = _db_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM clan.memories WHERE parent_id = %s AND metadata->>'status' = 'sprint'",
                (TICKETS_ROOT_ID,),
            )
            sprint_count = cur.fetchone()[0]
            conn.close()
            gate = _gate_tripped()
            return {
                "status": "healthy",
                "sprint_tickets": sprint_count,
                "gate_tripped": gate,
                "detail": f"{sprint_count} sprint tickets; gate={'tripped' if gate else 'clear'}",
                "checked_at": checked_at,
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "detail": str(exc),
                "checked_at": checked_at,
            }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._startup_errors)

    def logs(self) -> dict:
        return {"paths": {"trace": str(self._log_root / self.DEVICE_ID / "trace")}}

    def update_info(self) -> dict:
        return {"current_version": "1.0.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.environ.get("HOSTNAME", "localhost"),
            "pid": os.getpid(),
            "launch_command": "python -m devices.queue.mcp_server",
        }

    def restart(self) -> None:
        pass  # stateless — nothing to restart

    def block(self, reason: str) -> None:
        self.warning(f"queue device blocked: {reason}")

    def halt(self) -> None:
        self.warning("queue device halt requested")

    def recovery(self) -> None:
        self._startup_errors.clear()

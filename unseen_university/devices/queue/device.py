"""Queue device — work ticket queue served as a rack device.

All consumers (CC, Igor, Librarian) call queue_next(worker) → ticket | None
via MCP instead of calling cc_queue.py directly.

Backend: the filesystem ticket store (D-build-queue-filesystem-first-2026-06-19)
via unseen_university.ticket_store — no Postgres. This device is stateless; all
durable state lives in tickets/ + tickets/closed/.

There is no claim operation. queue_next() is atomic: it reads the next eligible
ticket and marks it in_progress via ticket_store.conditional_update (a race-safe
check-and-set under the store's mutation lock — the filesystem analogue of the
old SELECT ... FOR UPDATE). Any code that calls queue_claim() raises
LegacyDirectClaimError.
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
        # Verify the filesystem ticket store is readable (soft — device stays live).
        try:
            from unseen_university import ticket_store

            ticket_store.list()
        except Exception as exc:
            self._startup_errors.append(f"ticket store not readable at startup: {exc}")
            self.warning(f"startup: ticket store not readable — {exc}")

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

        from unseen_university import ticket_store

        # Candidate set: sprint, ungated, assigned to this worker (active only).
        candidates = [
            t
            for t in ticket_store.list(status_filter="sprint")
            if not t.get("gate") and t.get("worker") == worker
        ]
        if not candidates:
            return None

        best = min(candidates, key=_priority_key)
        ticket_id = best["id"]

        # Race-safe claim: conditional_update only writes if status is STILL 'sprint'
        # under the store's mutation lock (the FS analogue of SELECT ... FOR UPDATE).
        # None => another caller claimed it between our read and the lock — parity
        # with the old "return None" on a lost race.
        def _mark_in_progress(body: dict) -> dict:
            body["status"] = "in_progress"
            body["dispatched_at"] = datetime.now(timezone.utc).isoformat()
            return body

        try:
            path = ticket_store.conditional_update(
                ticket_id, expect_current="sprint", mutate=_mark_in_progress
            )
        except KeyError:
            return None  # vanished between list and claim
        if path is None:
            return None  # lost the race — status no longer 'sprint'

        self.trace_record("queue_next", {"worker": worker, "ticket_id": ticket_id})
        self.info(f"queue_next: dispatched {ticket_id!r} to worker={worker!r}")
        return ticket_store.read(ticket_id)

    def queue_peek(self, worker: str) -> dict | None:
        """Return the next eligible ticket without marking it in_progress.

        Read-only. Use this when you want to preview what would be dispatched
        without committing to working it.
        """
        if _gate_tripped():
            return None

        from unseen_university import ticket_store

        candidates = [
            t
            for t in ticket_store.list(status_filter="sprint")
            if not t.get("gate") and t.get("worker") == worker
        ]
        if not candidates:
            return None
        return min(candidates, key=_priority_key)

    def queue_show(self, ticket_id: str) -> dict | None:
        """Return a single ticket by ID (active or closed), or None if not found."""
        from unseen_university import ticket_store

        return ticket_store.read(ticket_id)

    def queue_list(
        self, worker: str | None = None, status: str = "sprint"
    ) -> list[dict]:
        """Return all tickets matching worker and status filters.

        worker=None returns tickets for all workers.
        status defaults to 'sprint' (ready-to-work tickets).
        """
        from unseen_university import ticket_store

        results = [
            t
            for t in ticket_store.list(status_filter=status, include_closed=True)
            if worker is None or t.get("worker") == worker
        ]
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
        return {"deps": ["unseen_university.ticket_store"]}

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
            from unseen_university import ticket_store

            sprint_count = len(ticket_store.list(status_filter="sprint"))
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
            "launch_command": "python -m unseen_university.devices.queue.mcp_server",
        }

    def restart(self) -> None:
        pass  # stateless — nothing to restart

    def block(self, reason: str) -> None:
        self.warning(f"queue device blocked: {reason}")

    def halt(self) -> None:
        self.warning("queue device halt requested")

    def recovery(self) -> None:
        self._startup_errors.clear()

"""
DickSimnelDevice — OR-powered autonomous ticket worker (worker tier).

DickSimnel is the Sonnet/worker-tier replacement for CC in the headless
automated pipeline. It:
  1. Polls cc_queue for sprint tickets assigned to worker=dicksimnel
  2. Claims one ticket at a time (respects concurrency limit)
  3. Routes the ticket through the inference proxy (worker task_class)
  4. Posts the inference result as a ticket note / closes with result

v0.1 scope: inference-backed analysis + ticket state management.
            Actual file-patching (code execution) is v0.2.

Availability:
  ~/.granny/available/DickSimnel.0.available.true  → Granny will offer tickets
  ~/.granny/available/DickSimnel.0.available.false → Granny skips DickSimnel
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unseen_university.device import BaseDevice, INTERFACE_VERSION

from .shim import DickSimnelShim

log = logging.getLogger(__name__)

_START_TIME = time.time()
_CC_QUEUE = Path(__file__).resolve().parents[2] / "lab" / "claudecode" / "cc_queue.py"
_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

SYSTEM_PROMPT = """\
You are DickSimnel, an autonomous software engineering agent in the UnseenUniversity rack.
Your task is to implement a sprint ticket from the queue.

For each ticket you receive:
1. Analyze the problem and affected files described in the ticket
2. Produce a concrete implementation plan with specific code changes
3. Write the actual code/changes needed
4. Identify what tests should be added or updated

Be specific and concrete. Your output will be used directly to implement the ticket.
Format your response as:
## Analysis
(what the ticket is asking for)

## Implementation
(specific code changes with file paths and line numbers)

## Tests
(what tests to add/update)

## Confidence
(high/medium/low — with reason if not high)
"""


class DickSimnelDevice(BaseDevice):
    """
    DickSimnel.0 — OR-powered sprint ticket worker.

    One active ticket at a time. Polls cc_queue every POLL_INTERVAL seconds
    for sprint tickets assigned to worker=dicksimnel.
    """

    DEVICE_ID = "dicksimnel"
    POLL_INTERVAL = 30

    def __init__(self) -> None:
        super().__init__()
        self._shim = DickSimnelShim(worker_callback=self._poll_and_work)
        self._active_ticket: str | None = None
        self._blocked = False
        self._block_reason = ""
        self._startup_errors: list[str] = []
        self._tickets_processed = 0
        self._tickets_declined = 0

    # ── BaseDevice contract ────────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "DickSimnel.0",
            "version": "0.1.0",
            "purpose": "OR-powered autonomous sprint ticket worker (worker tier)",
            "agent_class": "worker",
        }

    def health(self) -> dict:
        if self._blocked:
            return {"status": "unhealthy", "detail": f"blocked: {self._block_reason}", "checked_at": _now()}
        test = self._shim.self_test()
        if test.get("passed"):
            return {
                "status": "healthy",
                "detail": f"active={self._active_ticket or 'none'} processed={self._tickets_processed}",
                "checked_at": _now(),
            }
        return {"status": "degraded", "detail": test.get("details", "unknown"), "checked_at": _now()}

    def startup_errors(self) -> list:
        return self._startup_errors

    def requirements(self) -> dict:
        return {
            "deps": ["psycopg2", "devices.inference (inference proxy)"],
            "env": ["IGOR_HOME_DB_URL", "OPENROUTER_API_KEY or GOOGLE_AI_STUDIO_API_KEY"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": ["DICKSIMNEL_DONE", "DICKSIMNEL_DECLINE", "DICKSIMNEL_ERROR"],
            "task_class": "worker",
        }

    def comms(self) -> dict:
        return {"address": f"comms://{self.DEVICE_ID}/inbox", "mode": "read_write"}

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def logs(self) -> dict:
        return {"paths": {"device": str(Path.home() / ".unseen_university" / "dicksimnel" / "device.log")}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {"host": os.environ.get("HOSTNAME", "localhost"), "pid": os.getpid()}

    def restart(self) -> None:
        self._shim.stop()
        self._active_ticket = None
        self._blocked = False
        self._block_reason = ""
        self._shim.start()

    def block(self, reason: str) -> None:
        self._blocked = True
        self._block_reason = reason
        self._shim.stop()
        log.warning("DickSimnelDevice: blocked — %s", reason)

    def halt(self) -> None:
        self.block("halt requested")

    def recovery(self) -> None:
        self._blocked = False
        self._block_reason = ""
        self._active_ticket = None
        self._startup_errors = []

    # ── Queue interaction ──────────────────────────────────────────────────────

    def _run_queue_cmd(self, *args) -> dict | list | None:
        """Run cc_queue.py with args; return parsed JSON or None on error."""
        if not _CC_QUEUE.exists():
            log.warning("DickSimnel: cc_queue.py not found at %s", _CC_QUEUE)
            return None
        try:
            result = subprocess.run(
                ["python3", str(_CC_QUEUE)] + list(args),
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "IGOR_HOME_DB_URL": _DB_URL},
            )
            if result.returncode != 0:
                log.debug("DickSimnel: cc_queue %s failed: %s", args, result.stderr[:200])
                return None
            return json.loads(result.stdout)
        except (json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            log.debug("DickSimnel: cc_queue error: %s", exc)
            return None

    def _claim_next_ticket(self) -> dict | None:
        """Atomically claim the next sprint ticket assigned worker=dicksimnel.

        Uses cc_queue.py next --worker dicksimnel which marks in_progress and
        returns the ticket JSON. This is the canonical claim pattern — no
        separate find+claim race condition.

        Returns the ticket dict or None if no ticket is available.
        """
        try:
            result = subprocess.run(
                ["python3", str(_CC_QUEUE), "next", "--worker", "dicksimnel"],
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "IGOR_HOME_DB_URL": _DB_URL},
            )
            if result.returncode != 0:
                log.debug("DickSimnel: next --worker returned no ticket: %s", result.stderr[:100])
                return None
            ticket = json.loads(result.stdout)
            if not ticket:
                return None
            self._active_ticket = ticket.get("id")
            log.info("DickSimnel: claimed ticket %s", self._active_ticket)
            return ticket
        except (json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            log.debug("DickSimnel: claim failed: %s", exc)
            return None

    def _post_result(self, ticket_id: str, result_text: str) -> None:
        """Close ticket with inference result as the completion note."""
        # Truncate to avoid huge notes
        note = result_text[:2000]
        self._run_queue_cmd("close", ticket_id, f"DickSimnel.0: {note}")
        self._tickets_processed += 1
        log.info("DickSimnel: closed ticket %s", ticket_id)

    def _decline_ticket(self, ticket_id: str, reason: str) -> None:
        """Return ticket to sprint status with a decline note."""
        self._run_queue_cmd(
            "setstatus", ticket_id, "sprint",
        )
        log.info("DickSimnel: declined ticket %s — %s", ticket_id, reason)
        self._tickets_declined += 1
        self._active_ticket = None

    # ── Inference ─────────────────────────────────────────────────────────────

    def _run_inference(self, ticket: dict) -> str | None:
        """Run the ticket through the inference proxy (worker tier). Returns result text."""
        try:
            from devices.inference.device import InferenceDevice
            from devices.inference.shim import InferenceRequest

            description = ticket.get("description", ticket.get("title", "No description"))
            ticket_id = ticket.get("id", "?")

            prompt = (
                f"Ticket ID: {ticket_id}\n"
                f"Title: {ticket.get('title', 'No title')}\n"
                f"Size: {ticket.get('size', '?')}\n"
                f"Tags: {', '.join(ticket.get('tags', []))}\n\n"
                f"Description:\n{description}"
            )

            req = InferenceRequest(
                model="",  # let rules engine pick via task_class
                messages=[{"role": "user", "content": prompt}],
                system=SYSTEM_PROMPT,
                task_class="worker",
                agent_id="dicksimnel",
                max_tokens=4096,
                timeout=120,
            )

            device = InferenceDevice()
            log.info("DickSimnel: dispatching ticket %s to inference proxy", ticket_id)
            response = device.dispatch(req)
            log.info(
                "DickSimnel: inference done for %s — %d output tokens, $%.4f",
                ticket_id, response.output_tokens, response.cost_estimate,
            )
            return response.text

        except Exception as exc:
            log.error("DickSimnel: inference failed for ticket %s: %s", ticket.get("id", "?"), exc)
            return None

    # ── Main work cycle ────────────────────────────────────────────────────────

    def _poll_and_work(self) -> None:
        """Called by shim poll loop. Claim and work one ticket per cycle."""
        if self._blocked:
            return
        if self._shim.is_blocked():
            log.debug("DickSimnel: .false flag set — skipping this cycle")
            return
        if self._active_ticket is not None:
            log.debug("DickSimnel: ticket %s still active — skipping poll", self._active_ticket)
            return

        ticket = self._claim_next_ticket()
        if ticket is None:
            log.debug("DickSimnel: no tickets available for worker=dicksimnel")
            return

        ticket_id = ticket["id"]
        log.info("DickSimnel: working ticket %s — %s", ticket_id, ticket.get("title", "?"))

        result = self._run_inference(ticket)
        if result:
            self._post_result(ticket_id, result)
        else:
            self._decline_ticket(ticket_id, "inference proxy unavailable or returned empty")

        self._active_ticket = None

    # ── Start/stop (delegate to shim) ─────────────────────────────────────────

    def start(self) -> bool:
        return self._shim.start()

    def stop(self) -> bool:
        return self._shim.stop()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

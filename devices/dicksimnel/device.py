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
_SKILLS_DIR = Path.home() / ".claude" / "skills"
_HIGH_INERTIA_TAGS = frozenset({"Security", "Provenance", "Database", "Auth", "Brainstem"})

SYSTEM_PROMPT = """\
You are DickSimnel, an autonomous software engineering agent in the UnseenUniversity rack.
Work sprint tickets by reading, editing, and testing code.
Always use tools to take action — never describe what you plan to do without doing it.
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
            "emitted_keywords": ["DICKSIMNEL_WORKING", "DICKSIMNEL_DONE", "DICKSIMNEL_DECLINE", "DICKSIMNEL_ESCALATE", "DICKSIMNEL_ERROR"],
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
        returns the ticket ID (bare string). Then fetches the full ticket dict
        via cc_queue.py show <id>.

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
            ticket_id = result.stdout.strip()
            if not ticket_id:
                return None
            # next prints the bare ticket ID; fetch the full dict via show
            show = subprocess.run(
                ["python3", str(_CC_QUEUE), "show", ticket_id],
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "IGOR_HOME_DB_URL": _DB_URL},
            )
            if show.returncode == 0:
                ticket = json.loads(show.stdout)
            else:
                log.warning("DickSimnel: show failed for %s — working with minimal dict", ticket_id)
                ticket = {"id": ticket_id, "title": ticket_id, "description": ""}
            self._active_ticket = ticket.get("id") or ticket_id
            self._channel_event(
                f"DICKSIMNEL_WORKING ticket={self._active_ticket}"
                f" title={ticket.get('title', '?')!r}"
            )
            log.info("DickSimnel: claimed ticket %s", self._active_ticket)
            return ticket
        except (json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            log.warning("DickSimnel: claim failed: %s", exc)
            return None

    def _channel_event(self, message: str) -> None:
        """Post a lifecycle event to the shared channel. Non-fatal if unavailable."""
        try:
            from unseen_university.channel import post_to_channel
            post_to_channel(message, author="dicksimnel", channel="shared")
        except Exception as exc:
            log.warning("DickSimnel: channel post failed: %s", exc)

    def _post_result(self, ticket_id: str, result_text: str) -> None:
        """Close ticket with inference result as the completion note.

        Validates before closing:
        1. DONE: gate — ToolLoop must return text starting with 'DONE:'; anything
           else means the model stopped without completing the work. Escalate to CC.
        2. Close-failure guard — if cc_queue close() returns None (DB error or
           ticket not found), escalate rather than silently treating as done.
        """
        if not result_text.strip().startswith("DONE:"):
            log.warning(
                "DickSimnel: %s result missing DONE: prefix — escalating to CC",
                ticket_id,
            )
            self._escalate_ticket(ticket_id, "result missing DONE: prefix — not a completion", analysis=result_text)
            return

        note = result_text[:2000]
        close_result = self._run_queue_cmd("close", ticket_id, f"DickSimnel.0: {note}")
        if close_result is None:
            log.warning("DickSimnel: close failed for %s — escalating to CC", ticket_id)
            self._escalate_ticket(ticket_id, "close command failed", analysis=result_text[:300])
            return

        self._tickets_processed += 1
        self._channel_event(f"DICKSIMNEL_DONE ticket={ticket_id} summary={result_text[:100]!r}")
        log.info("DickSimnel: closed ticket %s", ticket_id)

    def _decline_ticket(self, ticket_id: str, reason: str) -> None:
        """Return ticket to sprint status with a decline note."""
        self._run_queue_cmd("setstatus", ticket_id, "sprint")
        self._channel_event(f"DICKSIMNEL_DECLINE ticket={ticket_id} reason={reason!r}")
        log.info("DickSimnel: declined ticket %s — %s", ticket_id, reason)
        self._tickets_declined += 1
        self._active_ticket = None

    def _escalate_ticket(self, ticket_id: str, reason: str, analysis: str = "") -> None:
        """Hand ticket off to CC with analysis note.

        Posts DICKSIMNEL_ESCALATE to shared channel, resets worker=claude,
        resets status=sprint. CC picks it up with Dick's analysis as context.
        """
        summary = (analysis[:300] + "...") if len(analysis) > 300 else analysis
        self._channel_event(
            f"DICKSIMNEL_ESCALATE ticket={ticket_id} reason={reason!r} analysis={summary!r}"
        )
        self._run_queue_cmd("set-worker", "claude", ticket_id)
        self._run_queue_cmd("setstatus", ticket_id, "sprint")
        log.info("DickSimnel: escalated ticket %s to CC — %s", ticket_id, reason)
        self._tickets_declined += 1
        self._active_ticket = None

    def _should_escalate(self, ticket: dict, result: str | None) -> tuple[bool, str]:
        """Return (True, reason) if this ticket should be escalated to CC.

        One trigger: HIGH-inertia tags present (checked pre-inference, saves cost).
        Post-inference quality gating is handled by the DONE: gate in _post_result.
        """
        tags = set(ticket.get("tags", []))
        inertia_hit = tags & _HIGH_INERTIA_TAGS
        if inertia_hit:
            return True, f"HIGH-inertia tags: {sorted(inertia_hit)}"
        return False, ""

    # ── Inference ─────────────────────────────────────────────────────────────

    def skill_load(self, name: str) -> str | None:
        """Load a skill file from ~/.claude/skills/<name>/SKILL.md.

        Returns the file content, or None if not found/unreadable.
        Best-effort — missing skills warn and fall back gracefully.
        """
        skill_path = _SKILLS_DIR / name / "SKILL.md"
        if not skill_path.exists():
            log.debug("DickSimnel: skill %r not found at %s", name, skill_path)
            return None
        try:
            content = skill_path.read_text()
            log.info("DickSimnel: loaded skill %r (%d chars)", name, len(content))
            return content
        except Exception as exc:
            log.warning("DickSimnel: skill load failed for %r: %s", name, exc)
            return None

    _IBD_PREAMBLE = (
        "Before executing the sprint-ticket skill steps below, do three things:\n"
        "1. State in one sentence the intention for this ticket: "
        "'I intend that...'\n"
        "2. State the hypothesis: what should be observably different when this ships?\n"
        "3. Write the test that validates the hypothesis.\n"
        "Then proceed with sprint-ticket as written.\n\n"
    )

    def _build_system_prompt(self, ticket: dict) -> str:
        """Build the system prompt. sprint-ticket skill is the sole procedural guide."""
        skill_content = self.skill_load("sprint-ticket")
        if skill_content:
            return (
                "You are DickSimnel, an autonomous software engineering agent in the "
                "UnseenUniversity rack. Execute the sprint-ticket procedure below exactly.\n\n"
                + self._IBD_PREAMBLE
                + skill_content
            )
        return SYSTEM_PROMPT

    def _run_inference(self, ticket: dict) -> str | None:
        """Work a ticket through the ReAct ToolLoop. Returns result text or None."""
        from devices.dicksimnel.toolloop import ToolLoop
        ticket_id = ticket.get("id", "?")
        log.info("DickSimnel: starting ToolLoop for ticket %s", ticket_id)
        try:
            loop = ToolLoop()
            result = loop.run(ticket, self._build_system_prompt(ticket))
            if result:
                log.info("DickSimnel: ToolLoop finished for %s (%d chars)", ticket_id, len(result))
            else:
                log.warning("DickSimnel: ToolLoop returned None for %s", ticket_id)
            return result
        except Exception as exc:
            log.error("DickSimnel: ToolLoop failed for ticket %s: %s", ticket_id, exc)
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

        # Pre-inference: bail on HIGH-inertia tags (saves cost; CC handles these)
        should_esc, esc_reason = self._should_escalate(ticket, None)
        if should_esc:
            self._escalate_ticket(ticket_id, esc_reason)
            return

        result = self._run_inference(ticket)
        if result is None:
            self._decline_ticket(ticket_id, "inference proxy unavailable or returned empty")
            return

        # Post-inference: check confidence level
        should_esc, esc_reason = self._should_escalate(ticket, result)
        if should_esc:
            self._escalate_ticket(ticket_id, esc_reason, analysis=result)
        else:
            self._post_result(ticket_id, result)

        self._active_ticket = None

    # ── Chat interface ─────────────────────────────────────────────────────────

    def chat(self, message: str) -> str:
        """Handle a direct message from Akien.

        Skill verbs (/help, /health, etc.) route to BaseShim.handle_command().
        Freeform text goes to a short conversational inference call.
        """
        message = message.strip()
        if message.startswith("/"):
            return self._shim.handle_command(message)
        return self._chat_inference(message)

    def _chat_inference(self, message: str) -> str:
        """Run a short conversational inference call. Returns response text."""
        try:
            from devices.inference.device import InferenceDevice
            from devices.inference.shim import InferenceRequest

            system = (
                "You are DickSimnel, an autonomous software engineering agent in the "
                "UnseenUniversity rack. You are in a direct conversation with Akien, "
                "your operator. Answer questions about your work, reasoning, and ticket "
                "status concisely. Current active ticket: "
                + (self._active_ticket or "none")
                + f". Tickets processed: {self._tickets_processed}."
                + f" Tickets escalated: {self._tickets_declined}."
            )
            req = InferenceRequest(
                model="",
                messages=[{"role": "user", "content": message}],
                system=system,
                task_class="worker",
                agent_id="dicksimnel",
                max_tokens=512,
                timeout=30,
            )
            response = InferenceDevice().dispatch(req)
            log.info("DickSimnel: chat response for %r — %d tokens", message[:40], response.output_tokens)
            return response.text
        except Exception as exc:
            log.warning("DickSimnel: chat inference failed: %s", exc)
            return f"DickSimnel: inference unavailable — {exc}"

    # ── Start/stop (delegate to shim) ─────────────────────────────────────────

    def start(self) -> bool:
        return self._shim.start()

    def stop(self) -> bool:
        return self._shim.stop()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

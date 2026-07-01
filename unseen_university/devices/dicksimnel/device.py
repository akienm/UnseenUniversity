"""
DickSimnelDevice — bus-dispatched autonomous ticket worker (worker tier).

DickSimnel receives sprint tickets via Granny bus dispatch envelopes on the
dicksimnel.0 mailbox. On dispatch:
  1. Sends dispatch_ack to Granny (handled by DickSimnelWorkerListener)
  2. Fetches the ticket, checks HIGH-inertia tags, runs inference
  3. Posts result (closes ticket) or escalates to CC

The device is dormant between dispatches — no polling.

v0.1 scope: inference-backed analysis + ticket state management.
            Actual file-patching (code execution) is v0.2.

Availability:
  ~/.granny/available/DickSimnel.0.available.true  → Granny will dispatch
  ~/.granny/available/DickSimnel.0.available.false → Granny skips DickSimnel
"""

from __future__ import annotations
from unseen_university.identity import home_db_url

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from unseen_university.device import BaseDevice, INTERFACE_VERSION

from .shim import DickSimnelShim

log = logging.getLogger(__name__)

_START_TIME = time.time()
_CC_QUEUE = Path(__file__).resolve().parents[2] / "devlab" / "claudecode" / "cc_queue.py"
_SKILLS_DIR = Path.home() / ".claude" / "skills"
_HIGH_INERTIA_TAGS = frozenset({"Security", "Provenance", "Database", "Auth", "Brainstem"})

# Source-down (availability) retries at the SAME difficulty before the driver halts. Small
# and hard — an infra blip re-selects next-cheapest, but a persistent outage must not loop
# (and must not walk onto paid tiers unbounded).
_MAX_AVAILABILITY_RETRIES = 2


def _classify_toolloop_result(result: str | None) -> str:
    """Classify a ToolLoop.run result for the escalation driver (T-router-failure-bump-escalation).

    The availability-vs-capability split is the whole safety of the walk — capability bumps to
    a pricier tier, availability must NOT. Returns one of:
      'availability' — the call did not reach a live source (None: dispatch raised, or the router
                       returned a no-source error response). NOT the model failing; do NOT bump.
      'cost'         — a paid run hit its per-run cost cap without finishing (COST_EXCEEDED:).
      'done'         — an explicit DONE terminal envelope (work claimed complete).
      'capability'   — reached a terminal but never DONE (self-ESCALATE, MAX_TURNS status=error,
                       or prose with no DONE): the tier could not finish → bump difficulty.
    """
    from unseen_university.devices.dicksimnel.toolloop import _parse_terminal_response
    if result is None:
        return "availability"
    stripped = result.strip()
    if stripped.startswith("COST_EXCEEDED:"):
        return "cost"
    env = _parse_terminal_response(stripped)
    if env is not None and env.get("status") == "done":
        return "done"
    return "capability"

# The DS builder/coding system prompt now lives as DATA in the inference
# router's domain-prompt store (prompts/coding.md), resolved by domain — the
# router routes BOTH model and prompt by domain (T-inference-domain-prompt).
# SYSTEM_PROMPT stays as a byte-identical alias for existing importers/tests.
from unseen_university.devices.inference.domain_prompts import domain_prompt

SYSTEM_PROMPT = domain_prompt("coding")

class DickSimnelDevice(BaseDevice):
    """
    DickSimnel.0 — bus-dispatched sprint ticket worker.

    Dormant between dispatches. Granny sends {kind:dispatch, ticket_id} to
    dicksimnel.0; the shim's DickSimnelWorkerListener wakes, works one ticket
    synchronously, and returns to listening.
    """

    DEVICE_ID = "dicksimnel"

    def __init__(self) -> None:
        super().__init__()
        self._shim = DickSimnelShim(device=self)
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
            "deps": ["psycopg2", "unseen_university.devices.inference (inference proxy)"],
            "env": ["UU_HOME_DB_URL", "OPENROUTER_API_KEY or GOOGLE_AI_STUDIO_API_KEY"],
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

    def _fetch_ticket(self, ticket_id: str) -> dict | None:
        """Fetch full ticket dict by ID. Returns None on error or not found."""
        try:
            result = subprocess.run(
                ["python3", str(_CC_QUEUE), "show", ticket_id],
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "UU_HOME_DB_URL": home_db_url()},
            )
            if result.returncode != 0:
                log.warning("DickSimnel: show failed for %s: %s", ticket_id, result.stderr[:100])
                return None
            return json.loads(result.stdout)
        except (json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            log.warning("DickSimnel: fetch_ticket error for %s: %s", ticket_id, exc)
            return None

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
                env={**os.environ, "UU_HOME_DB_URL": home_db_url()},
            )
            if result.returncode != 0:
                log.debug("DickSimnel: cc_queue %s failed: %s", args, result.stderr[:200])
                return None
            return json.loads(result.stdout)
        except (json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            log.debug("DickSimnel: cc_queue error: %s", exc)
            return None

    # Importance levels per event type (D-feeds-taxonomy-2026-06-11).
    _EVENT_IMPORTANCE = {
        "dispatch_received": 5,
        "working": 3,
        "done": 7,
        "escalated": 7,
        "decline": 5,
        "error": 9,
    }
    _PERSONAL_FEED = "dicksimnel/personal"

    def _bus(self):
        """Return a started PgBus connection. Cached per-process."""
        if not hasattr(self, "_bus_conn"):
            from unseen_university.devices.bus.connection import make_bus_connection
            self._bus_conn = make_bus_connection()
            self._bus_conn.create_mailbox(self._PERSONAL_FEED, feed_type="personal")
        return self._bus_conn

    def _channel_event(self, message: str, event_type: str = "working") -> None:
        """Post a lifecycle event to Dick's personal feed + shared channel.

        event_type selects the importance level from _EVENT_IMPORTANCE.
        Falls back to shared channel post if bus is unavailable.
        """
        importance = self._EVENT_IMPORTANCE.get(event_type, 3)
        try:
            from unseen_university.devices.bus.envelope import Envelope
            bus = self._bus()
            env = Envelope.now(
                from_device="dicksimnel.0",
                to_device=self._PERSONAL_FEED,
                payload={"event": message, "kind": event_type},
            )
            env.importance = importance
            bus.append(self._PERSONAL_FEED, env)
            log.info("DickSimnel: posted to %s importance=%d event=%s", self._PERSONAL_FEED, importance, event_type)
        except Exception as exc:
            log.warning("DickSimnel: bus post failed (%s), falling back to channel: %s", event_type, exc)
            try:
                from unseen_university.channel import post_to_channel
                post_to_channel(message, author="dicksimnel", channel="shared")
            except Exception as exc2:
                log.warning("DickSimnel: channel fallback also failed: %s", exc2)

    def _post_result(self, ticket_id: str, result_text: str) -> None:
        """Close ticket with inference result as the completion note.

        Validates before closing:
        1. DONE: gate — ToolLoop must return text starting with 'DONE:'; anything
           else means the model stopped without completing the work. Escalate to CC.
        2. Close-failure guard — if cc_queue close() returns None (DB error or
           ticket not found), escalate rather than silently treating as done.
        """
        if result_text.strip().startswith("MAX_TURNS:"):
            log.warning(
                "DickSimnel: %s hit max turns — escalating to CC",
                ticket_id,
            )
            self._escalate_ticket(ticket_id, "max turns hit without completing", analysis=result_text)
            return

        if result_text.strip().startswith("COST_EXCEEDED:"):
            log.warning(
                "DickSimnel: %s cost cap hit — escalating to CC: %s",
                ticket_id, result_text.strip()[:120],
            )
            self._escalate_ticket(ticket_id, result_text.strip()[:200], analysis=result_text)
            return

        if result_text.strip().startswith("ESCALATE:"):
            reason = result_text.strip()[len("ESCALATE:"):].strip()[:200]
            log.warning("DickSimnel: %s self-escalating — %s", ticket_id, reason)
            self._escalate_ticket(ticket_id, f"self-escalated: {reason}", analysis=result_text)
            return

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
            # close() returns None on error and when ticket is already closed.
            # Check the actual status before escalating — double-close is success.
            show_result = self._run_queue_cmd("show", ticket_id)
            if show_result and show_result.get("status") in ("done", "closed"):
                log.info("DickSimnel: ticket %s already closed — treating as success", ticket_id)
            else:
                log.warning("DickSimnel: close failed for %s — escalating to CC", ticket_id)
                self._escalate_ticket(ticket_id, "close command failed", analysis=result_text[:300])
                return

        self._tickets_processed += 1
        self._channel_event(f"DICKSIMNEL_DONE ticket={ticket_id} summary={result_text[:100]!r}", event_type="done")
        log.info("DickSimnel: closed ticket %s", ticket_id)

    def _decline_ticket(self, ticket_id: str, reason: str) -> None:
        """Return ticket to sprint status with a decline note."""
        self._run_queue_cmd("setstatus", ticket_id, "sprint")
        self._channel_event(f"DICKSIMNEL_DECLINE ticket={ticket_id} reason={reason!r}", event_type="decline")
        log.info("DickSimnel: declined ticket %s — %s", ticket_id, reason)
        self._tickets_declined += 1
        self._active_ticket = None

    def _escalate_ticket(self, ticket_id: str, reason: str, analysis: str = "") -> None:
        """Hand ticket off to CC with structured escalation summary.

        Appends a structured summary to the ticket body so CC starts informed,
        then posts to channel and sets status=escalated.
        """
        tried = (analysis[:500] + "...") if len(analysis) > 500 else analysis or "(no analysis captured)"
        summary_block = (
            "## Escalation summary\n"
            f"**What was tried:** {tried}\n"
            f"**Where it broke:** {reason}\n"
            "**What now?**"
        )
        self._run_queue_cmd("append-note", ticket_id, summary_block)
        self._channel_event(
            f"DICKSIMNEL_ESCALATE ticket={ticket_id} reason={reason!r}",
            event_type="escalated",
        )
        self._run_queue_cmd("setstatus", ticket_id, "escalated")
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
        """Build system prompt for the ToolLoop, resolved by task DOMAIN.

        DS is a coding worker, so it resolves the 'coding' domain prompt from the
        router's domain-prompt store (data, not baked in — T-inference-domain-prompt).
        This is the compact builder prompt (not the full sprint-ticket skill) so OR
        models don't narrate the workflow instead of calling tools. The sprint-ticket
        skill is CC-specific (memory_get, mcp__*, Agent) and too long (~9k chars)
        for OR models — they interpret it as a workflow to explain, not execute.
        """
        return domain_prompt("coding")

    # Internal tier cascade: cheapest first, escalate within Dick before going to CC.
    # Each tier appends context from the prior attempt so the next model starts informed.
    def _run_inference(self, ticket: dict) -> str | None:
        """Work a ticket through the ReAct ToolLoop, driving the ONE escalation walk.

        This is the single live escalation driver (T-router-failure-bump-escalation),
        collapsing the three former half-mechanisms — the never-incremented device
        escalation_hop, the degenerate _TIER_CASCADE, and the ad-hoc prior-attempt append
        — into one difficulty walk:

          - CAPABILITY failure (reached a terminal but never produced a DONE: self-ESCALATE,
            MAX_TURNS, or prose without DONE): bump difficulty ONE rung (escalation_hop+1)
            and re-run the SAME domain-aware selector, which picks a more-capable (pricier)
            tier. This is the ONLY trigger that spends up.
          - AVAILABILITY failure (dispatch raised, or the router held no live source →
            ToolLoop returns None): NOT escalation — re-select next-cheapest at the SAME
            difficulty (bounded), 'Hex-DOWN is not a branch'. A system_alarm fires if
            retries exhaust.
          - Past the top difficulty rung for the domain: inference failure → system_alarm →
            HALT for analysis. Checked BEFORE re-dispatch so the walk terminates cleanly and
            never loops into the device's hop-ceiling backstop.
          - COST_EXCEEDED (a paid run hit its per-run cost cap without finishing): halt —
            bumping to a pricier tier would only cost more.

        Cumulative per-ticket cost is bounded by the small attempt count (≤2 capability hops
        for the worker→design range, plus ≤2 availability retries) and is observable per
        ticket in the cost_record log (T-inference-cost-learn-verify). Returns the DONE result
        on success, or None to HALT (worker_listener declines; a system_alarm has fired). The
        old return-to-CC endpoint is gone: capability failures escalate UP the domain's own
        tiers, not out to CC.
        """
        from unseen_university.devices.dicksimnel.toolloop import ToolLoop
        from unseen_university.devices.inference.routing_buckets import (
            bump_difficulty, task_class_to_difficulty,
        )
        from unseen_university import system_alarms

        ticket_id = ticket.get("id", "?")
        system_prompt = self._build_system_prompt(ticket)
        base_difficulty = task_class_to_difficulty("worker")  # 'code'
        escalation_hop = 0
        prior_attempt = ""
        availability_retries = 0

        while True:
            # Terminal check BEFORE dispatch: bumped past the top difficulty rung → inference
            # failure. Firing here (not after another dispatch) is what stops the walk looping.
            required = bump_difficulty(base_difficulty, escalation_hop)
            if required is None:
                system_alarms.raise_alarm(
                    signature=f"inference-capability-ceiling:{ticket_id}",
                    caller="dicksimnel",
                    message=(
                        f"capability ceiling for ticket {ticket_id}: escalated past the top "
                        f"difficulty tier ('{base_difficulty}'+{escalation_hop}) and still no DONE "
                        f"— inference failure, halting for analysis"
                    ),
                    fatal=False,
                )
                log.error(
                    "DickSimnel: capability ceiling for %s (hop=%d) — halting for analysis",
                    ticket_id, escalation_hop,
                )
                return None

            log.info(
                "DickSimnel: inference attempt ticket=%s hop=%d difficulty=%s",
                ticket_id, escalation_hop, required,
            )
            try:
                loop = ToolLoop()
                result = loop.run(
                    ticket, system_prompt,
                    escalation_hop=escalation_hop, prior_attempt=prior_attempt,
                )
            except Exception as exc:
                # Any raise (incl. OllamaCloudFatalError) is an AVAILABILITY failure — a source
                # went down mid-call. Treat as None so the walk re-selects, never bumps to paid.
                log.error(
                    "DickSimnel: ToolLoop raised for %s (hop=%d): %s", ticket_id, escalation_hop, exc
                )
                result = None

            cls = _classify_toolloop_result(result)
            log.info(
                "DickSimnel: attempt classified ticket=%s hop=%d class=%s", ticket_id, escalation_hop, cls
            )

            if cls == "done":
                log.info(
                    "DickSimnel: DONE for %s at hop=%d difficulty=%s", ticket_id, escalation_hop, required
                )
                return result

            if cls == "cost":
                system_alarms.raise_alarm(
                    signature=f"inference-cost-cap:{ticket_id}",
                    caller="dicksimnel",
                    message=(
                        f"cost cap hit for ticket {ticket_id} without completing — halting "
                        f"(bumping to a pricier tier would only cost more)"
                    ),
                    fatal=False,
                )
                log.error("DickSimnel: cost cap for %s — halting: %s", ticket_id, (result or "").strip()[:120])
                return None

            if cls == "availability":
                availability_retries += 1
                if availability_retries > _MAX_AVAILABILITY_RETRIES:
                    system_alarms.raise_alarm(
                        signature=f"inference-availability-exhausted:{ticket_id}",
                        caller="dicksimnel",
                        message=(
                            f"no live source for ticket {ticket_id} after {_MAX_AVAILABILITY_RETRIES} "
                            f"retries at difficulty '{required}' — halting"
                        ),
                        fatal=False,
                    )
                    log.error("DickSimnel: availability exhausted for %s — halting", ticket_id)
                    return None
                log.warning(
                    "DickSimnel: availability failure for %s (retry %d/%d, same difficulty=%s) — "
                    "re-selecting next-cheapest",
                    ticket_id, availability_retries, _MAX_AVAILABILITY_RETRIES, required,
                )
                continue  # same hop → the selector skips the down source and picks next-cheapest

            # cls == 'capability': reached a terminal but never DONE → bump difficulty one rung.
            prior_attempt = (result or "").strip()[:400]
            escalation_hop += 1
            log.info(
                "DickSimnel: capability failure for %s at difficulty=%s — bumping to hop=%d",
                ticket_id, required, escalation_hop,
            )

    def replay_and_analyze(self, ticket_id: str) -> dict:
        """Replay a closed ticket using the simulator to understand decision-making.

        Args:
            ticket_id: ID of a closed ticket with recorded logs

        Returns:
            Analysis dict with keys:
              - event_count: number of turns recorded
              - decision_points: list of places where DickSimnel could diverge
              - success_rate: fraction of tool calls that succeeded
              - turns: list of turn details (tool_name, tool_args, outcome)
        """
        from unseen_university.devices.dicksimnel.simulator import TicketSimulator

        log.info("DickSimnel: replay_and_analyze for %s", ticket_id)
        try:
            sim = TicketSimulator(ticket_id)
            events = list(sim.replay_all())

            if not events:
                log.warning("DickSimnel: no events found for %s", ticket_id)
                return {
                    "ticket_id": ticket_id,
                    "event_count": 0,
                    "decision_points": [],
                    "success_rate": 0.0,
                    "turns": [],
                }

            # Collect turn details
            turns = []
            for event in events:
                turns.append({
                    "turn": event.turn_num,
                    "timestamp": event.timestamp,
                    "decision_point": event.decision_point,
                    "tool": event.tool_name,
                    "tool_result": event.tool_result,  # Actual error/success message
                    "outcome": event.outcome,
                })

            # Extract decision points
            decision_points = sim.decision_points()

            # Compute success rate
            success_rate = sim.success_rate()

            result = {
                "ticket_id": ticket_id,
                "event_count": len(events),
                "decision_points": decision_points,
                "success_rate": success_rate,
                "turns": turns,
            }

            log.info(
                "DickSimnel: replay_and_analyze complete for %s — %d events, %.1f%% success",
                ticket_id, len(events), success_rate * 100,
            )
            return result
        except Exception as exc:
            log.error("DickSimnel: replay_and_analyze failed for %s: %s", ticket_id, exc)
            return {
                "ticket_id": ticket_id,
                "error": str(exc),
            }

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
            from unseen_university.devices.inference.device import InferenceDevice
            from unseen_university.devices.inference.shim import InferenceRequest

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
                foreground=True,
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

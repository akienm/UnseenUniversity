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
_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_SKILLS_DIR = Path.home() / ".claude" / "skills"
_HIGH_INERTIA_TAGS = frozenset({"Security", "Provenance", "Database", "Auth", "Brainstem"})

# Capability map prepended to the sprint-ticket skill when loaded.
# Tells OR models (Bash/Read/Edit/Write only) how to execute CC-specific steps.
_CAPABILITY_MAP = """\
## OUTPUT CONSTRAINT — ABSOLUTE

Your only permitted text output is one of:
  DONE: <one-line summary>
  ESCALATE: <reason>

Any other text — prose, narration, plans, explanations — is a protocol
violation that triggers re-dispatch at higher model cost. Do not explain
yourself. Do not summarize in prose. Call tools until the work is done,
then output DONE: and stop.

## Your execution environment

You are DickSimnel, a sprint-ticket worker. You have exactly four tools:
Bash, Read, Edit, Write. Working directory: ~/dev/src/UnseenUniversity

## Tool mappings — when the sprint-ticket skill says X, do Y instead

  memory_get(path="P")
    → Bash: psql $UU_HOME_DB_URL -tAc "SELECT content FROM memory_palace WHERE path='P'"

  mcp__igor__* / mcp__datacenter__* / mcp__librarian__*
    → SKIP — MCP not wired yet; continue to the next step

  /audit-precode, /audit-hypothesis, /audit-ticket (sub-skill invocations)
    → SKIP — skill invocation unavailable; proceed

  Agent tool / subagent spawn (e.g. step 8.5 grader)
    → SKIP — no subagent capability; proceed to next step

  python run X
    → Bash: cd ~/dev/src/UnseenUniversity && python run X

  ${CC_WORKFLOW_TOOLS}/X.py  OR  python3 ${CC_WORKFLOW_TOOLS}/X.py
    → Bash: python3 ~/dev/src/UnseenUniversity/lab/claudecode/X.py

  /savestate, /autocompact
    → SKIP — session skills unavailable; output DONE: after ticket close instead

  Step 3 "select executor": always execute inline — never delegate

## Execution discipline

- Call tools immediately. NEVER narrate or plan in prose — if you would describe
  a bash command, call Bash with it instead.
- Your only text output (outside tool calls) is the final DONE: or ESCALATE: line.

## Completion

After step 11 (close ticket):
  DONE: <one-line summary of what was built>

If blocked (scope unclear, HIGH-inertia file, missing context):
  ESCALATE: <reason>

"""

SYSTEM_PROMPT = _CAPABILITY_MAP + """\
## Workflow

Your FIRST ACTION must be a tool call — read the ticket, then explore, implement, test, commit, close.

1. Bash: python3 ~/dev/src/UnseenUniversity/lab/claudecode/cc_queue.py show <ticket_id>
2. Bash + Read: explore relevant source files to understand scope
3. Edit/Write: implement the change
4. Bash: cd ~/dev/src/UnseenUniversity && source .venv/bin/activate && python -m pytest tests/ -q --tb=short 2>&1 | tail -20
5. Bash: git add <specific-files> && git pull --rebase origin main && git push origin main
   (commit message: "feat/fix: description\\n\\nCo-Authored-By: DickSimnel (devstral-small-2:24b) <noreply@anthropic.com>")
6. Bash: python3 ~/dev/src/UnseenUniversity/lab/claudecode/cc_queue.py close <ticket_id> "<one-line summary>"
7. Output (no tool call): DONE: <one-line summary>

Rules:
- ALWAYS call a tool first — never start with prose
- NEVER skip tests (step 4) — green run required before commit
- NEVER git add -A or git add . — always name specific files
- If scope is unclear or touches HIGH-inertia files: output ESCALATE: <reason>
"""


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
            "deps": ["psycopg2", "devices.inference (inference proxy)"],
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
                env={**os.environ, "UU_HOME_DB_URL": _DB_URL},
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
                env={**os.environ, "UU_HOME_DB_URL": _DB_URL},
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
            from bus.connection import make_bus_connection
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
            from bus.envelope import Envelope
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
        """Build system prompt for the ToolLoop.

        Uses the compact SYSTEM_PROMPT (not the full sprint-ticket skill) so OR
        models don't narrate the workflow instead of calling tools. The sprint-ticket
        skill is CC-specific (memory_get, mcp__*, Agent) and too long (~9k chars)
        for OR models — they interpret it as a workflow to explain, not execute.
        """
        return SYSTEM_PROMPT

    # Internal tier cascade: cheapest first, escalate within Dick before going to CC.
    # Each tier appends context from the prior attempt so the next model starts informed.
    # Tier cascade: builder → creator → (CC). Each explicit ESCALATE: advances to the next
    # tier (with context appended) rather than going straight to CC. Only the last tier's
    # ESCALATE: propagates to CC. Creator tier absorbs escalations that builder can't handle
    # but that don't need master (CC/Anthropic) attention.
    #
    # Tuple: (model_id, tier_label)
    # tier_label "builder" — cheap floor; first attempt
    # tier_label "creator" — mid-tier; receives builder escalation context
    #
    # OLLAMA-ONLY MODE: OR tiers disabled — no paid fallthrough. Escalate to CC if devstral fails.
    _TIER_CASCADE = [
        ("devstral-small-2:24b", "builder"),        # tier 0: flat-rate floor; purpose-built agentic coding
        # Creator tier — larger OR model; absorbs builder escalations before reaching CC
        # Enable when OR/paid inference is back on.
        # ("qwen/qwen3-30b-a3b-instruct", "creator"),  # DISABLED — OR off
        # ("anthropic/claude-haiku-4.5", "creator"),   # DISABLED — OR off
    ]

    def _run_inference(self, ticket: dict) -> str | None:
        """Work a ticket through the ReAct ToolLoop with internal tier escalation.

        Tries tiers in _TIER_CASCADE order. ESCALATE: from a non-final tier advances
        to the next tier with the escalation context appended — builder→creator before
        reaching CC. DONE: or COST_EXCEEDED: terminate immediately. MAX_TURNS: with
        tool calls escalates to CC directly (no tier advance — tier was working, ran out
        of turns, adding context to a bigger model won't help).

        Returns the last result (for CC escalation) or None if all tiers fail hard.
        """
        from devices.dicksimnel.toolloop import ToolLoop
        ticket_id = ticket.get("id", "?")
        system_prompt = self._build_system_prompt(ticket)
        last_result: str | None = None

        for idx, (model_id, tier_label) in enumerate(self._TIER_CASCADE):
            is_last_tier = (idx == len(self._TIER_CASCADE) - 1)
            log.info("DickSimnel: ToolLoop tier=%s model=%r for ticket %s", tier_label, model_id or "rules-engine", ticket_id)
            try:
                loop = ToolLoop()
                result = loop.run(ticket, system_prompt, model_override=model_id)
                if result:
                    log.info("DickSimnel: tier=%s finished for %s (%d chars)", tier_label, ticket_id, len(result))
                    last_result = result
                    stripped = result.strip()
                    # DONE: / COST_EXCEEDED: terminate the cascade immediately.
                    if stripped.startswith("DONE:") or stripped.startswith("COST_EXCEEDED:"):
                        return result
                    # ESCALATE: — if more tiers remain, advance with context; otherwise hand to CC.
                    if stripped.startswith("ESCALATE:"):
                        if is_last_tier:
                            return result  # CC handles it
                        reason = stripped[len("ESCALATE:"):].strip()[:300]
                        esc_note = (
                            f"\n\n---\n**{tier_label} tier escalation:**\n"
                            f"Reason: {reason}\n"
                            f"Attempt: {result[:400]}"
                        )
                        ticket = dict(ticket)
                        ticket["description"] = ticket.get("description", "") + esc_note
                        log.info(
                            "DickSimnel: tier=%s escalated for %s — advancing to next tier with context",
                            tier_label, ticket_id,
                        )
                        continue
                    # MAX_TURNS: with tool calls = tier was working but ran out of turns.
                    # Don't advance to a more expensive tier — escalate to CC with the log.
                    had_tool_calls = any(e.get("had_tool_calls") for e in loop._turn_log)
                    if stripped.startswith("MAX_TURNS:") and had_tool_calls:
                        log.warning(
                            "DickSimnel: tier=%s hit MAX_TURNS with tool calls for %s — escalating to CC (not advancing tier)",
                            tier_label, ticket_id,
                        )
                        return result
                    # No DONE:, no tool calls (blank stall or planning-mode prose) — try next tier.
                    log.warning(
                        "DickSimnel: tier=%s no DONE: for %s — trying next tier (prior attempt appended)",
                        tier_label, ticket_id,
                    )
                    prior_note = f"\n\n---\n**Prior attempt ({tier_label}) produced no DONE: result.**\n{result[:300]}"
                    ticket = dict(ticket)
                    ticket["description"] = ticket.get("description", "") + prior_note
                else:
                    log.warning("DickSimnel: tier=%s returned None for %s — trying next tier", tier_label, ticket_id)
            except Exception as exc:
                from devices.inference.sources import OllamaCloudFatalError
                if isinstance(exc, OllamaCloudFatalError):
                    # Ollama Cloud failed hard — don't fall through to OR/paid sources.
                    log.error(
                        "DickSimnel: tier=%s OllamaCloudFatalError for %s — halting cascade: %s",
                        tier_label, ticket_id, exc,
                    )
                    # Return an escalation envelope so the caller escalates to CC
                    import json as _json
                    return _json.dumps({
                        "status": "escalate",
                        "result": f"OllamaCloud fatal error on tier {tier_label}: {exc}",
                        "error_class": "OLLAMA_CLOUD_FATAL",
                        "error_number": None,
                    })
                log.error("DickSimnel: tier=%s failed for %s: %s", tier_label, ticket_id, exc)

        # All tiers tried — return last result (will be escalated to CC by caller)
        log.warning("DickSimnel: all tiers exhausted for %s — escalating to CC", ticket_id)
        return last_result

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
        from devices.dicksimnel.simulator import TicketSimulator

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

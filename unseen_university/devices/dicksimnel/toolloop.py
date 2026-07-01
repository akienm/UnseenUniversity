"""
ToolLoop — multi-turn ReAct inference loop using native OR tool calling.

DickSimnel uses ToolLoop to work sprint tickets: inference reasons about
the ticket, calls tools (Bash/Read/Edit/Write) via the standard OpenAI
tool-use protocol, sees results, and continues until the model stops
calling tools (finish_reason='stop') or hits the turn cap.

Tool call format: standard OpenAI function-calling (tools= in request,
tool_calls in response). No XML parsing.

Termination:
  response.tool_calls is None/empty  — model finished; text is the result
  None return                         — inference failed or timed out
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

MAX_TURNS = 50
MAX_TURNS_FLAT_RATE = 80  # flat-rate sources don't pay per turn; give more budget
DONE_PREFIX = "DONE:"  # legacy — kept for backwards-compat parsing only
COST_CAP_USD = 3.00  # only enforced for usage_based sources

# Bash commands blocked by the safety denylist
_BASH_DENYLIST = re.compile(
    r"\brm\s+-rf\b"
    r"|\bgit\s+push\s+--force\b"
    r"|\bgit\s+reset\s+--hard\b"
    r"|\bgit\s+checkout\s+--\b"
    r"|\bdd\s+if=\b"
    r"|\bsudo\s+rm\b"
    r"|\bgit\s+add\s+-A\b"
    r"|\bgit\s+add\s+\.\b",
    re.IGNORECASE,
)

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": "Execute a shell command. Returns stdout+stderr (first 2000 chars).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": "Read a file. Returns content (first 3000 chars).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to file"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Edit",
            "description": "Replace exact text in a file. old_string must be unique in the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Write",
            "description": "Write a complete file (creates or overwrites).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
        },
    },
]

SYSTEM_RULES = """\
## Exit protocol

When finished, respond with a JSON envelope (no prose, no other text):
{"status": "done", "result": "<one-line summary of what was done>", "error_class": null, "error_number": null}

When escalating, respond with:
{"status": "escalate", "result": "<reason>", "error_class": "ESCALATE", "error_number": null}

No other non-tool responses are valid.

## Rules

- Run tests before commit: Bash `.venv/bin/python3 -m pytest tests/ -q --tb=short 2>&1 | tail -15`
- Stage by name only — never git add -A or git add .
- Commit message must include: Co-Authored-By: DickSimnel (devstral-small-2:24b) <noreply@anthropic.com>
- Never force-push
- Use absolute paths for Read/Edit/Write
"""


def _orientation_prefix(ticket: dict) -> str:
    """
    Call the orientation classifier and return its text block (or '' on failure).
    Fail-open: any exception → empty string, ToolLoop continues without it.
    Interface crossing: INFO log with match count.
    """
    try:
        from unseen_university.devices.scraps.orientation_classifier import classify
        report = classify(ticket)
        if report.relevant_files:
            text = report.to_text()
            log.info("ToolLoop builder_report: %d relevant files for %s",
                     len(report.relevant_files), ticket.get("id", "?"))
            return text + "\n\n"
    except Exception as exc:
        log.warning("ToolLoop builder_report failed for %s: %s", ticket.get("id", "?"), exc)
    return ""


def _build_initial_message(ticket: dict, builder_report_text: str = "") -> str:
    """Build the first user message for the ToolLoop from a ticket dict."""
    ticket_id = ticket.get("id", "?")
    return (
        f"{builder_report_text}"
        f"Ticket ID: {ticket_id}\n"
        f"Title: {ticket.get('title', 'No title')}\n"
        f"Tags: {', '.join(ticket.get('tags', []))}\n\n"
        f"Description:\n{ticket.get('description', ticket.get('title', ''))}"
    )


class ToolLoop:
    """Multi-turn ReAct inference loop using native OR tool calling."""

    def __init__(self, max_turns: int = MAX_TURNS) -> None:
        """max_turns caps inference cost — hit it means escalation, not silent truncation."""
        self._max_turns = max_turns
        self._turn_log: list[dict] = []

    def run(self, ticket: dict, system_prompt: str, model_override: str = "") -> str | None:
        """Work a ticket through the tool loop.

        Returns the model's final text when it stops calling tools, or the
        last assistant content if max_turns is hit, or None if inference failed.

        Populates self._turn_log after each run — list of dicts with keys:
          turn (int), had_tool_calls (bool), tool_names (list[str])
        Cleared at the start of each run() call.
        """
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        self._turn_log = []
        ticket_id = ticket.get("id", "?")
        running_cost: float = 0.0
        source_billing_type: str = "usage_based"  # updated on first response
        inference_device = InferenceDevice()  # Cache once per run, not per turn

        # Initialize Critic for advisory rule application and decision evaluation.
        # _load_rules() is called in CriticDevice.__init__ — prior learning carries forward.
        # Fail-open: if Critic unavailable, sprint continues unaffected.
        critic = None
        critic_judgments: list = []
        try:
            from unseen_university.devices.critic.device import CriticDevice
            from unseen_university.devices.critic.agent import Decision as CriticDecision
            critic = CriticDevice()
            log.info("ToolLoop: Critic initialized for %s (%d prior rules)",
                     ticket_id, len(critic._agent.export_rules()))
        except Exception as exc:
            log.debug("ToolLoop: critic init skipped (non-fatal): %s", exc)

        def _critic_finalize() -> None:
            """Analyze patterns + learn rules at sprint end. Non-fatal."""
            if critic is None or not critic_judgments:
                return
            try:
                analysis = critic._agent.analyze_pattern(critic_judgments)
                critic.learn_from_critic(analysis)
                log.info(
                    "ToolLoop: critic_finalize|ticket=%s|judgments=%d|rules_saved=%d",
                    ticket_id, len(critic_judgments), critic._agent.get_stats()["rules_learned"],
                )
            except Exception as exc:
                log.debug("ToolLoop: critic_finalize failed (non-fatal): %s", exc)

        # Prepend builder report from orientation classifier (fail-open)
        builder_report_text = _orientation_prefix(ticket)

        user_msg = _build_initial_message(ticket, builder_report_text)
        messages = [{"role": "user", "content": user_msg}]
        full_system = system_prompt + "\n\n" + SYSTEM_RULES

        effective_max_turns = self._max_turns
        turn = 0
        while turn < effective_max_turns:
            log.info("ToolLoop turn %d/%d $%.4f — ticket %s",
                     turn + 1, effective_max_turns, running_cost, ticket_id)
            # Force tool use on turn 1 to prevent planning-mode narration.
            # After turn 1, auto lets the model decide (including returning DONE:).
            extra = {"tool_choice": "required"} if turn == 0 else {}
            req = InferenceRequest(
                model=model_override,
                messages=messages,
                system=full_system,
                tools=TOOL_DEFINITIONS,
                task_class="worker",
                domain="coding",  # DS builds code — route among coding-capable sources only
                ticket_id=ticket_id,  # per-ticket correlator for the cost+outcome record
                agent_id="dicksimnel",
                max_tokens=4096,
                timeout=120,
                temperature=0.0,
                extra=extra,
                foreground=False,  # flat-rate (Ollama Cloud) preferred; foreground=True would flip to OR
            )
            try:
                response = inference_device.dispatch(req)
            except Exception as exc:
                log.error("ToolLoop inference failed on turn %d: %s", turn + 1, exc)
                return None  # inference error — no critic finalize (no data)

            # On first response, lock in billing type and adjust turn cap accordingly.
            if turn == 0 and response.source_billing_type == "flat_rate":
                source_billing_type = "flat_rate"
                effective_max_turns = max(effective_max_turns, MAX_TURNS_FLAT_RATE)
                log.info(
                    "ToolLoop: flat_rate source detected — raising turn cap to %d for %s",
                    effective_max_turns, ticket_id,
                )

            if source_billing_type == "flat_rate":
                # Flat-rate: no per-token cost; skip cost cap entirely.
                pass
            else:
                running_cost += response.cost_estimate
                if running_cost >= COST_CAP_USD:
                    log.warning(
                        "ToolLoop: cost cap hit $%.4f/$%.2f on turn %d for %s",
                        running_cost, COST_CAP_USD, turn + 1, ticket_id,
                    )
                    _critic_finalize()
                    return f"COST_EXCEEDED: ${running_cost:.2f} of ${COST_CAP_USD:.2f} cap — inference did not complete within cost constraint"

            tool_calls = response.tool_calls
            log.debug(
                "ToolLoop turn %d: %d chars, %d tool calls, $%.4f cumulative",
                turn + 1,
                len(response.text or ""),
                len(tool_calls) if tool_calls else 0,
                running_cost,
            )
            self._turn_log.append({
                "turn": turn + 1,
                "had_tool_calls": bool(tool_calls),
                "tool_names": [
                    tc.get("function", {}).get("name", "") for tc in (tool_calls or [])
                ],
                "cost": response.cost_estimate,
            })

            if not tool_calls:
                envelope = _parse_terminal_response(response.text or "")
                if turn == 0 and envelope is None:
                    # Turn 1 returned no tools and no terminal envelope — model went into planning mode.
                    # Inject a correction and continue so it will actually call tools.
                    log.warning("ToolLoop: turn 1 no tools for %s — injecting correction", ticket_id)
                    messages.append({"role": "assistant", "content": response.text or ""})
                    messages.append({
                        "role": "user",
                        "content": (
                            "You described what you plan to do, but you must call a tool "
                            "to take action. Please begin now — read a file, run a command, "
                            "or make an edit."
                        ),
                    })
                    turn += 1
                    continue
                log.info("ToolLoop: done on turn %d for %s envelope_status=%s",
                         turn + 1, ticket_id, (envelope or {}).get("status", "legacy_prose"))
                _critic_finalize()
                return response.text

            # Append the assistant message (content may be null on tool-call turns)
            messages.append({
                "role": "assistant",
                "content": response.text or None,
                "tool_calls": tool_calls,
            })

            # Execute each tool and return results as role:tool messages
            for tc in tool_calls:
                tc_id = tc.get("id", "")
                fn = tc.get("function", {})
                name = fn.get("name", "")
                # Tool-call arguments come as a JSON string (OpenAI) OR an already-parsed
                # dict (Ollama /api/chat). Accept both — json.loads on a dict raises
                # TypeError, not JSONDecodeError, so it must be handled explicitly.
                raw_args = fn.get("arguments", "{}")
                if isinstance(raw_args, dict):
                    args = raw_args
                else:
                    try:
                        args = json.loads(raw_args)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                # Advisory: apply prior critic rules before executing this tool call.
                if critic is not None:
                    try:
                        ctx = {"decision_point": "tool_selection", "tool_name": name, "turn": turn + 1}
                        rec = critic.get_recommendation(ctx)
                        if rec:
                            log.info(
                                "ToolLoop: Critic advisory|ticket=%s|turn=%d|rule=%s|action=%s",
                                ticket_id, turn + 1, rec["rule"], rec["action"][:80],
                            )
                    except Exception:
                        pass

                result = _execute_tool(name, args)
                log.info("ToolLoop: %s → %d chars result", name, len(result))

                # Evaluate this decision point — verdict logged at INFO in CriticAgent.
                if critic is not None:
                    try:
                        decision = CriticDecision(
                            ticket_id=ticket_id,
                            turn_num=turn + 1,
                            decision_point="tool_selection",
                            choice=name,
                            context={"ticket_id": ticket_id, "turn": turn + 1, "tool_args": args},
                            tool_result=result[:200],
                        )
                        judgment = critic._agent.evaluate_decision(decision)
                        critic_judgments.append(judgment)
                    except Exception:
                        pass

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                })

            turn += 1

        log.warning("ToolLoop: hit max turns (%d) for %s", effective_max_turns, ticket_id)
        _critic_finalize()
        return json.dumps({
            "status": "error",
            "result": f"MAX_TURNS: hit {effective_max_turns} turns without terminal response",
            "error_class": "MAX_TURNS",
            "error_number": effective_max_turns,
        })


# ── Response parsing ─────────────────────────────────────────────────────────


def _parse_terminal_response(text: str) -> dict | None:
    """
    Parse a terminal (no-tool-calls) response from the model.

    Returns a dict with at least {"status": ...} when the response is a valid
    terminal envelope (JSON or legacy DONE:/ESCALATE: prefix).
    Returns None when the response is not a terminal signal (planning mode, etc.).

    Interface crossing log: caller logs envelope_status at INFO.
    """
    stripped = text.strip()
    if not stripped:
        return None

    # JSON envelope (new protocol)
    if stripped.startswith("{"):
        try:
            envelope = json.loads(stripped)
            if isinstance(envelope, dict) and "status" in envelope:
                return envelope
        except json.JSONDecodeError:
            pass

    # Legacy DONE: / ESCALATE: prefix (backwards compat)
    if stripped.startswith("DONE:"):
        return {"status": "done", "result": stripped[5:].strip(), "error_class": None, "error_number": None}
    if stripped.upper().startswith("ESCALATE:"):
        return {"status": "escalate", "result": stripped[9:].strip(), "error_class": "ESCALATE", "error_number": None}

    return None


# ── Tool dispatch ─────────────────────────────────────────────────────────────


def _execute_tool(name: str, args: dict) -> str:
    """Route a tool call to the appropriate handler; return string result for the model."""
    try:
        if name == "Bash":
            return _tool_bash(args.get("command", ""))
        if name == "Read":
            return _tool_read(args.get("path", ""))
        if name == "Edit":
            return _tool_edit(args)
        if name == "Write":
            return _tool_write(args)
        return f"ERROR: unknown tool {name!r}"
    except Exception as exc:
        log.warning("ToolLoop _execute_tool %s raised: %s", name, exc)
        return f"ERROR: {exc}"


def _tool_bash(command: str) -> str:
    """Run a shell command; denylist blocks destructive patterns before subprocess is called."""
    if _BASH_DENYLIST.search(command):
        log.warning("ToolLoop Bash denylist blocked: %r", command[:80])
        return "ERROR: command blocked by safety denylist"
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=_REPO_ROOT,
        )
        out = (result.stdout + result.stderr)[:2000]
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out (60s)"


def _tool_read(path: str) -> str:
    """Read and return a file's content (truncated to 3000 chars to stay inside model context)."""
    p = Path(path.strip())
    if not p.exists():
        return f"ERROR: file not found: {p}"
    try:
        content = p.read_text(errors="replace")
        return content[:3000] + ("...(truncated)" if len(content) > 3000 else "")
    except Exception as exc:
        log.warning("ToolLoop Read failed for %s: %s", p, exc)
        return f"ERROR: {exc}"


def _tool_edit(args: dict) -> str:
    """Exact-string replacement; rejects non-unique old_string to prevent silent mass-edits."""
    file_path = args.get("file_path", "")
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    if not file_path:
        return "ERROR: file_path required"
    p = Path(file_path)
    if not p.exists():
        return f"ERROR: file not found: {p}"
    try:
        content = p.read_text()
        count = content.count(old_string)
        if count == 0:
            return f"ERROR: old_string not found in {p}"
        if count > 1:
            return f"ERROR: old_string matches {count} locations — must be unique"
        p.write_text(content.replace(old_string, new_string, 1))
        return f"OK: edited {p}"
    except Exception as exc:
        log.warning("ToolLoop Edit failed for %s: %s", file_path, exc)
        return f"ERROR: {exc}"


def _tool_write(args: dict) -> str:
    """Write a complete file, creating parent dirs; overwrites any existing content."""
    file_path = args.get("file_path", "")
    content = args.get("content", "")
    if not file_path:
        return "ERROR: file_path required"
    try:
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"OK: wrote {len(content)} chars to {p}"
    except Exception as exc:
        log.warning("ToolLoop Write failed for %s: %s", file_path, exc)
        return f"ERROR: {exc}"

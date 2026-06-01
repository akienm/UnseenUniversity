"""
ToolLoop — inference + tool execution loop for the minion worker.

Round-based structure (default):
  - Up to ITERATIONS_PER_ROUND work iterations per round
  - After a round ends without a terminal signal, an ADVISOR CALL reviews progress
  - Advisor returns one of 6 signals: CONTINUE / REPROMPT / UPGRADE / BLOCKED / CONFUSED / ESCALATE
  - Context resets between rounds (advisor summary replaces full tool history)
  - Max MAX_ROUNDS rounds before hard ESCALATE

Legacy mode (max_iterations set explicitly):
  - Single round, no advisor, backwards-compatible with tests

Tool call format (XML-tagged, works for multiline content):
  <tool>Read</tool><path>path/to/file</path>
  <tool>Bash</tool><command>pytest tests/ -q</command>
  <tool>Edit</tool><path>f</path><old_string>...</old_string><new_string>...</new_string>
  <tool>Write</tool><path>f</path><content>...</content>

Terminal signals (anywhere in response text):
  DONE: <one-line summary>
  ESCALATE: worker|analyst|designer
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from devices.inference.device import InferenceDevice
from devices.inference.models_registry import default_registry as _default_registry
from devices.inference.shim import InferenceRequest
from devices.minion.shim import WorkerEnvelope, WorkerResult

log = logging.getLogger(__name__)

_ITERATIONS_PER_ROUND = int(os.environ.get("MINION_ITERATIONS_PER_ROUND", "5"))
_MAX_ROUNDS = int(os.environ.get("MINION_MAX_ROUNDS", "2"))
# Legacy env var — if set, disables round-based mode (single round, no advisor)
_LEGACY_MAX_ITERATIONS = os.environ.get("MINION_MAX_ITERATIONS")


def _cost_from_tokens(model_id: str, input_tokens: int, output_tokens: int) -> float:
    spec = _default_registry().get(model_id)
    return spec.cost_estimate(input_tokens, output_tokens) if spec else 0.0


_SYSTEM_PROMPT = """You are a MINION-tier code worker in the Unseen University agent rack.

TIER: minion
TASK: Complete one ticket. Make targeted edits, run tests, commit, close.

ESCALATION — respond with a signal line if you hit a wall:
  ESCALATE: worker   — after 3 failed attempts on any subtask
  ESCALATE: analyst  — ticket requires design decisions or cross-file reasoning
  ESCALATE: designer — safety/auth/multi-device coordination, or scope is wrong

Put the escalation signal on its own line, then explain: what you tried, what blocked you.

DONE SIGNAL — when tests pass and the commit is made:
  DONE: <one-line summary of what changed>

TOOL CALLS — output ONE tool call block per response, then stop and wait for the result:

  Read a file:
  <tool>Read</tool><path>path/to/file</path>

  Run a shell command:
  <tool>Bash</tool><command>pytest tests/ -q --tb=short 2>&1 | head -60</command>

  Edit a file (replace exact text — whitespace must match exactly):
  <tool>Edit</tool><path>path/to/file</path>
  <old_string>exact text to replace including whitespace</old_string>
  <new_string>replacement text</new_string>

  Write a new file:
  <tool>Write</tool><path>path/to/file</path>
  <content>full file content here</content>

RULES:
- One tool call per turn. Wait for the result before the next.
- Always run tests before DONE: python3 -m pytest tests/ -q --tb=short 2>&1 | head -60
- After 3 consecutive non-zero bash exits on the same issue: ESCALATE: worker
- Commit with: git add <files> && git commit -m "feat: <summary>"
- Never push — the rack handles that.

CONTEXT (repo map):
{repo_map}

TICKET: {ticket_id}
{description}"""

_ADVISOR_SYSTEM_PROMPT = """You are an advisor reviewing a code worker's progress on a ticket.
Your job is to assess whether work is progressing and what should happen next.

Output EXACTLY ONE signal line. No preamble, no explanation outside the signal.

Signals:
  CONTINUE
    Work is progressing. Worker should continue with the same approach.

  REPROMPT: <improved description>
    The ticket description was ambiguous. Provide a complete, clearer rewrite
    the worker can use in the next round. Must be self-contained.

  UPGRADE
    Task genuinely exceeds this worker tier's capability. Needs a higher tier.

  BLOCKED: <reason>
    Stuck on a missing external dependency (no DB, missing env var, file absent).
    Not a capability problem — a setup problem.

  CONFUSED
    You cannot determine what was attempted or why it failed.

  ESCALATE
    Task is beyond the capability path entirely. Requires human CC review."""


def _parse_tool_call(text: str) -> dict | None:
    """Extract the first tool call from LLM response. Returns None if not found."""
    tool_m = re.search(r"<tool>(\w+)</tool>", text, re.IGNORECASE)
    if not tool_m:
        return None
    tool = tool_m.group(1).title()

    if tool == "Read":
        m = re.search(r"<path>(.*?)</path>", text, re.DOTALL)
        if m:
            return {"tool": "Read", "path": m.group(1).strip()}

    elif tool == "Bash":
        m = re.search(r"<command>(.*?)</command>", text, re.DOTALL)
        if m:
            return {"tool": "Bash", "command": m.group(1).strip()}

    elif tool == "Edit":
        pm = re.search(r"<path>(.*?)</path>", text, re.DOTALL)
        om = re.search(r"<old_string>(.*?)</old_string>", text, re.DOTALL)
        nm = re.search(r"<new_string>(.*?)</new_string>", text, re.DOTALL)
        if pm and om and nm:
            return {
                "tool": "Edit",
                "path": pm.group(1).strip(),
                "old_string": om.group(1),
                "new_string": nm.group(1),
            }

    elif tool == "Write":
        pm = re.search(r"<path>(.*?)</path>", text, re.DOTALL)
        cm = re.search(r"<content>(.*?)</content>", text, re.DOTALL)
        if pm and cm:
            return {
                "tool": "Write",
                "path": pm.group(1).strip(),
                "content": cm.group(1),
            }

    return None


def _parse_signal(text: str) -> tuple[str, str] | None:
    """Check for DONE or ESCALATE terminal signal. Returns (signal, notes) or None."""
    done_m = re.search(r"\bDONE:\s*(.+)", text)
    if done_m:
        return ("DONE", done_m.group(1).strip())

    esc_m = re.search(r"\bESCALATE:\s*(worker|analyst|designer)\b", text, re.IGNORECASE)
    if esc_m:
        tier = esc_m.group(1).lower()
        reason = text[esc_m.end() : esc_m.end() + 600].strip()
        return (f"ESCALATE: {tier}", reason)

    return None


def _parse_advisor_signal(text: str) -> tuple[str, str]:
    """Parse advisor response into (signal, notes).

    Returns one of: CONTINUE / REPROMPT / UPGRADE / BLOCKED / CONFUSED / ESCALATE
    Falls back to CONFUSED when no recognised signal found.
    """
    text = text.strip()

    if re.match(r"^CONTINUE\b", text, re.IGNORECASE):
        return ("CONTINUE", "")

    m = re.match(r"^REPROMPT:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    if m:
        return ("REPROMPT", m.group(1).strip())

    if re.match(r"^UPGRADE\b", text, re.IGNORECASE):
        return ("UPGRADE", text[6:].strip())

    m = re.match(r"^BLOCKED:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    if m:
        return ("BLOCKED", m.group(1).strip())

    if re.match(r"^CONFUSED\b", text, re.IGNORECASE):
        return ("CONFUSED", text[8:].strip())

    if re.match(r"^ESCALATE\b", text, re.IGNORECASE):
        return ("ESCALATE", text[8:].strip())

    # Unrecognised — treat as CONFUSED
    log.warning(
        "_parse_advisor_signal: unrecognised response %r — treating as CONFUSED",
        text[:120],
    )
    return ("CONFUSED", f"Unrecognised advisor response: {text[:200]}")


def _compress_history(messages: list[dict], tools_called: list[str]) -> str:
    """Produce a compact summary of a work round for the advisor."""
    tool_summary = ", ".join(tools_called) if tools_called else "none"
    # Grab last assistant message (most recent work attempt)
    last_assistant = ""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_assistant = msg["content"][:500]
            break
    # Grab last tool result
    last_tool_result = ""
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg["content"].startswith("["):
            last_tool_result = msg["content"][:300]
            break
    return (
        f"Tools called: {tool_summary}\n"
        f"Last response: {last_assistant}\n"
        f"Last tool result: {last_tool_result}"
    )


def _execute_tool(action: dict, cwd: Path) -> str:
    """Execute a parsed tool action. Returns a result string fed back to the LLM."""
    tool = action["tool"]

    if tool == "Read":
        try:
            p = Path(action["path"])
            if not p.is_absolute():
                p = cwd / p
            content = p.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            if len(lines) > 200:
                content = (
                    "\n".join(lines[:200])
                    + f"\n... ({len(lines) - 200} lines truncated)"
                )
            return f"[Read {action['path']}]\n{content}"
        except Exception as exc:
            return f"[Read ERROR] {exc}"

    elif tool == "Bash":
        try:
            result = subprocess.run(
                action["command"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=cwd,
            )
            out = (result.stdout + result.stderr)[:3000]
            return f"[Bash rc={result.returncode}]\n{out}"
        except subprocess.TimeoutExpired:
            return "[Bash ERROR] command timed out after 120s"
        except Exception as exc:
            return f"[Bash ERROR] {exc}"

    elif tool == "Edit":
        try:
            p = Path(action["path"])
            if not p.is_absolute():
                p = cwd / p
            content = p.read_text(encoding="utf-8")
            old, new = action["old_string"], action["new_string"]
            if old not in content:
                return f"[Edit ERROR] old_string not found in {action['path']} — check exact whitespace"
            updated = content.replace(old, new, 1)
            p.write_text(updated, encoding="utf-8")
            return f"[Edit OK] {action['path']}: replaced {len(old)} chars with {len(new)} chars"
        except Exception as exc:
            return f"[Edit ERROR] {exc}"

    elif tool == "Write":
        try:
            p = Path(action["path"])
            if not p.is_absolute():
                p = cwd / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(action["content"], encoding="utf-8")
            return (
                f"[Write OK] {action['path']}: {len(action['content'])} chars written"
            )
        except Exception as exc:
            return f"[Write ERROR] {exc}"

    return f"[Unknown tool: {tool}]"


class ToolLoop:
    """
    Drives the inference + tool execution loop for a single WorkerEnvelope.

    Default (round-based) mode:
      - Up to iterations_per_round work iterations per round
      - Advisor call between rounds with 6-signal routing
      - Up to max_rounds rounds before hard ESCALATE

    Legacy mode (when max_iterations is set explicitly):
      - Single round, no advisor, backwards-compatible behaviour
    """

    def __init__(
        self,
        inference: InferenceDevice,
        cwd: Path | None = None,
        max_iterations: int | None = None,
        iterations_per_round: int = _ITERATIONS_PER_ROUND,
        max_rounds: int = _MAX_ROUNDS,
    ) -> None:
        self._inference = inference
        self._default_cwd = cwd or Path.cwd()

        if max_iterations is not None:
            # Legacy mode: single round, no advisor
            self._iterations_per_round = max_iterations
            self._max_rounds = 1
            self._advisor_enabled = False
        else:
            self._iterations_per_round = iterations_per_round
            self._max_rounds = max_rounds
            self._advisor_enabled = True

    def run(self, envelope: WorkerEnvelope) -> WorkerResult:
        """Run the loop for one ticket. Returns WorkerResult."""
        cwd = Path(envelope.cwd) if envelope.cwd else self._default_cwd
        description = envelope.description

        system = _SYSTEM_PROMPT.format(
            repo_map=envelope.repo_map or "(not available — read files you need)",
            ticket_id=envelope.ticket_id,
            description=description,
        )

        total_input_tokens = 0
        total_output_tokens = 0
        total_cost_usd = 0.0
        all_tools_called: list[str] = []
        total_iterations = 0
        advisor_calls = 0
        advisor_signal: str | None = None

        for round_num in range(1, self._max_rounds + 1):
            log.info(
                "ToolLoop: round %d/%d ticket=%s advisor=%s",
                round_num,
                self._max_rounds,
                envelope.ticket_id,
                self._advisor_enabled,
            )

            round_result = self._run_work_round(
                system=system,
                envelope=envelope,
                cwd=cwd,
                round_num=round_num,
            )

            total_iterations += round_result["iterations"]
            total_input_tokens += round_result["input_tokens"]
            total_output_tokens += round_result["output_tokens"]
            total_cost_usd += round_result["cost_usd"]
            all_tools_called.extend(round_result["tools_called"])

            # Terminal signal from the work round — return immediately
            if round_result["signal"] is not None:
                signal, notes = round_result["signal"]
                log.info("ToolLoop: terminal signal %r round=%d", signal, round_num)
                return WorkerResult(
                    signal=signal,
                    notes=notes,
                    iterations=total_iterations,
                    tools_called=all_tools_called,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    cost_usd=total_cost_usd,
                    round_count=round_num,
                    advisor_calls=advisor_calls,
                    advisor_signal=advisor_signal,
                )

            # Round exhausted without terminal signal
            if not self._advisor_enabled or round_num == self._max_rounds:
                break

            # Call advisor between rounds
            advisor_calls += 1
            history_summary = _compress_history(
                round_result["messages"], round_result["tools_called"]
            )
            adv_signal, adv_notes = self._call_advisor(
                envelope=envelope,
                description=description,
                history_summary=history_summary,
                round_num=round_num,
            )
            advisor_signal = adv_signal
            total_input_tokens += round_result.get("advisor_input_tokens", 0)
            total_output_tokens += round_result.get("advisor_output_tokens", 0)

            log.info(
                "ToolLoop: advisor signal %r round=%d ticket=%s",
                adv_signal,
                round_num,
                envelope.ticket_id,
            )

            if adv_signal == "CONTINUE":
                # Rebuild system prompt with same description, continue
                system = _SYSTEM_PROMPT.format(
                    repo_map=envelope.repo_map
                    or "(not available — read files you need)",
                    ticket_id=envelope.ticket_id,
                    description=description,
                )

            elif adv_signal == "REPROMPT":
                description = adv_notes  # advisor-rewritten description
                system = _SYSTEM_PROMPT.format(
                    repo_map=envelope.repo_map
                    or "(not available — read files you need)",
                    ticket_id=envelope.ticket_id,
                    description=description,
                )
                log.info(
                    "ToolLoop: REPROMPT — using advisor-rewritten description for round %d",
                    round_num + 1,
                )

            elif adv_signal == "UPGRADE":
                return WorkerResult(
                    signal="ESCALATE: analyst",
                    notes=(
                        f"UPGRADE: {adv_notes}"
                        if adv_notes
                        else "Advisor: task exceeds worker tier"
                    ),
                    iterations=total_iterations,
                    tools_called=all_tools_called,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    cost_usd=total_cost_usd,
                    round_count=round_num,
                    advisor_calls=advisor_calls,
                    advisor_signal=adv_signal,
                )

            elif adv_signal == "BLOCKED":
                return WorkerResult(
                    signal="ESCALATE: worker",
                    notes=f"BLOCKED: {adv_notes}",
                    iterations=total_iterations,
                    tools_called=all_tools_called,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    cost_usd=total_cost_usd,
                    round_count=round_num,
                    advisor_calls=advisor_calls,
                    advisor_signal=adv_signal,
                )

            else:
                # CONFUSED or ESCALATE — stop immediately
                escalate_notes = (
                    adv_notes
                    or f"Advisor signalled {adv_signal} after round {round_num}"
                )
                if adv_signal == "CONFUSED":
                    escalate_notes = (
                        f"CONFUSED: {adv_notes}"
                        if adv_notes
                        else "CONFUSED: advisor could not interpret work history"
                    )
                return WorkerResult(
                    signal="ESCALATE: worker",
                    notes=escalate_notes,
                    iterations=total_iterations,
                    tools_called=all_tools_called,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    cost_usd=total_cost_usd,
                    round_count=round_num,
                    advisor_calls=advisor_calls,
                    advisor_signal=adv_signal,
                )

        # All rounds exhausted without terminal signal
        return WorkerResult(
            signal="ESCALATE: worker",
            notes=f"Reached max iterations ({total_iterations}) without completing.",
            iterations=total_iterations,
            tools_called=all_tools_called,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cost_usd=total_cost_usd,
            round_count=self._max_rounds,
            advisor_calls=advisor_calls,
            advisor_signal=advisor_signal,
        )

    def _run_work_round(
        self,
        system: str,
        envelope: WorkerEnvelope,
        cwd: Path,
        round_num: int,
    ) -> dict:
        """Run one round of work iterations. Returns a result dict (not WorkerResult)."""
        messages: list[dict] = [
            {
                "role": "user",
                "content": (
                    f"Begin working on ticket {envelope.ticket_id}."
                    if round_num == 1
                    else f"Continue working on ticket {envelope.ticket_id}. Round {round_num}."
                ),
            }
        ]

        tools_called: list[str] = []
        iterations = 0
        consecutive_bash_failures = 0
        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0
        terminal_signal = None

        while iterations < self._iterations_per_round:
            iterations += 1
            log.info(
                "ToolLoop: iter %d/%d round=%d ticket=%s",
                iterations,
                self._iterations_per_round,
                round_num,
                envelope.ticket_id,
            )

            try:
                req = InferenceRequest(
                    messages=list(
                        messages
                    ),  # snapshot — list is mutated after dispatch
                    system=system,
                    task_class=envelope.task_class,
                    session_id=envelope.session_id,
                    max_tokens=4096,
                )
                resp = self._inference.dispatch(req)
                text = resp.text
            except Exception as exc:
                log.error("ToolLoop: inference error iter %d: %s", iterations, exc)
                terminal_signal = (
                    "ESCALATE: worker",
                    f"Inference error on iteration {iterations}: {exc}",
                )
                break

            input_tokens += resp.input_tokens
            output_tokens += resp.output_tokens
            iter_cost = _cost_from_tokens(
                resp.model, resp.input_tokens, resp.output_tokens
            )
            cost_usd += iter_cost if iter_cost > 0 else resp.cost_estimate

            messages.append({"role": "assistant", "content": text})

            sig = _parse_signal(text)
            if sig:
                terminal_signal = sig
                log.info("ToolLoop: signal %r round=%d", sig[0], round_num)
                break

            action = _parse_tool_call(text)
            if action is None:
                log.warning("ToolLoop: no tool call or signal (iter %d)", iterations)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "No tool call detected. Please output a tool call using the format "
                            "in your instructions, or signal DONE: or ESCALATE:."
                        ),
                    }
                )
                continue

            tools_called.append(action["tool"])
            result = _execute_tool(action, cwd)
            log.info("ToolLoop: %s → %s", action["tool"], result[:80])

            if action["tool"] == "Bash":
                rc_m = re.search(r"\[Bash rc=(\d+)\]", result)
                if rc_m and int(rc_m.group(1)) != 0:
                    consecutive_bash_failures += 1
                    if consecutive_bash_failures >= 3:
                        result += "\n[HINT: 3 consecutive non-zero exits — consider ESCALATE: worker if stuck]"
                else:
                    consecutive_bash_failures = 0

            messages.append({"role": "user", "content": result})

        return {
            "signal": terminal_signal,
            "iterations": iterations,
            "tools_called": tools_called,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "messages": messages,
        }

    def _call_advisor(
        self,
        envelope: WorkerEnvelope,
        description: str,
        history_summary: str,
        round_num: int,
    ) -> tuple[str, str]:
        """Call the advisor model and return (signal, notes)."""
        advisor_prompt = (
            f"TICKET: {envelope.ticket_id}\n"
            f"ORIGINAL DESCRIPTION:\n{description}\n\n"
            f"ROUND {round_num} WORK ATTEMPTED:\n{history_summary}\n\n"
            "What signal do you return?"
        )
        try:
            req = InferenceRequest(
                messages=[{"role": "user", "content": advisor_prompt}],
                system=_ADVISOR_SYSTEM_PROMPT,
                task_class=envelope.task_class,
                session_id=f"{envelope.session_id}-advisor",
                max_tokens=1024,
            )
            resp = self._inference.dispatch(req)
            return _parse_advisor_signal(resp.text)
        except Exception as exc:
            log.error("ToolLoop: advisor call failed: %s", exc)
            return ("CONFUSED", f"Advisor inference error: {exc}")

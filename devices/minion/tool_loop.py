"""
ToolLoop — inference + tool execution loop for the minion worker.

Each iteration:
  1. Send conversation history to InferenceDevice.dispatch()
  2. Parse response for a tool call (<tool>...</tool>) or terminal signal (DONE/ESCALATE)
  3. Execute the tool, append result to conversation
  4. Repeat until signal or max_iterations

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
from devices.inference.shim import InferenceRequest
from devices.minion.shim import WorkerEnvelope, WorkerResult

log = logging.getLogger(__name__)

_MAX_ITERATIONS = int(os.environ.get("MINION_MAX_ITERATIONS", "20"))

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


def _parse_tool_call(text: str) -> dict | None:
    """Extract the first tool call from LLM response. Returns None if not found."""
    tool_m = re.search(r"<tool>(\w+)</tool>", text, re.IGNORECASE)
    if not tool_m:
        return None
    tool = tool_m.group(1).title()  # normalise case

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

    Returns a WorkerResult when the LLM signals DONE/ESCALATE or iterations
    are exhausted.
    """

    def __init__(
        self,
        inference: InferenceDevice,
        cwd: Path | None = None,
        max_iterations: int = _MAX_ITERATIONS,
    ) -> None:
        self._inference = inference
        self._default_cwd = cwd or Path.cwd()
        self._max_iterations = max_iterations

    def run(self, envelope: WorkerEnvelope) -> WorkerResult:
        """Run the loop for one ticket. Returns WorkerResult."""
        cwd = Path(envelope.cwd) if envelope.cwd else self._default_cwd

        system = _SYSTEM_PROMPT.format(
            repo_map=envelope.repo_map or "(not available — read files you need)",
            ticket_id=envelope.ticket_id,
            description=envelope.description,
        )

        messages: list[dict] = [
            {
                "role": "user",
                "content": f"Begin working on ticket {envelope.ticket_id}.",
            }
        ]

        tools_called: list[str] = []
        iterations = 0
        consecutive_bash_failures = 0

        while iterations < self._max_iterations:
            iterations += 1
            log.info(
                "ToolLoop: iter %d/%d ticket=%s",
                iterations,
                self._max_iterations,
                envelope.ticket_id,
            )

            try:
                req = InferenceRequest(
                    messages=messages,
                    system=system,
                    task_class="worker",
                    session_id=envelope.session_id,
                    max_tokens=4096,
                )
                resp = self._inference.dispatch(req)
                text = resp.text
            except Exception as exc:
                log.error("ToolLoop: inference error iter %d: %s", iterations, exc)
                return WorkerResult(
                    signal="ESCALATE: worker",
                    notes=f"Inference error on iteration {iterations}: {exc}",
                    iterations=iterations,
                    tools_called=tools_called,
                )

            messages.append({"role": "assistant", "content": text})

            # Terminal signal takes precedence over tool calls
            sig = _parse_signal(text)
            if sig:
                signal, notes = sig
                log.info("ToolLoop: signal %r for %s", signal, envelope.ticket_id)
                return WorkerResult(
                    signal=signal,
                    notes=notes,
                    iterations=iterations,
                    tools_called=tools_called,
                )

            # Parse and execute tool call
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

            # Track bash failures for escalation nudge
            if action["tool"] == "Bash":
                rc_m = re.search(r"\[Bash rc=(\d+)\]", result)
                if rc_m and int(rc_m.group(1)) != 0:
                    consecutive_bash_failures += 1
                    if consecutive_bash_failures >= 3:
                        result += "\n[HINT: 3 consecutive non-zero exits — consider ESCALATE: worker if stuck]"
                else:
                    consecutive_bash_failures = 0

            messages.append({"role": "user", "content": result})

        return WorkerResult(
            signal="ESCALATE: worker",
            notes=f"Reached max iterations ({self._max_iterations}) without completing.",
            iterations=iterations,
            tools_called=tools_called,
        )

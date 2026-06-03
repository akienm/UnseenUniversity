"""
ToolLoop — multi-turn ReAct inference loop with tool execution.

DickSimnel uses ToolLoop to work sprint tickets: inference reasons about
the ticket, calls tools (Bash/Read/Edit/Write), sees results, and continues
until it emits a DONE signal or hits the turn cap.

Tool call format in inference output:
  <TOOL:Bash>shell command</TOOL>
  <TOOL:Read>/absolute/path/to/file.py</TOOL>
  <TOOL:Edit>{"file_path": "...", "old_string": "...", "new_string": "..."}</TOOL>
  <TOOL:Write>{"file_path": "...", "content": "..."}</TOOL>

Termination:
  DONE: <summary>   — successful completion; summary becomes the ticket close note
  None return       — inference failed or timed out
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

MAX_TURNS = 20
DONE_PREFIX = "DONE:"

_TOOL_PATTERN = re.compile(r"<TOOL:(\w+)>(.*?)</TOOL>", re.DOTALL)

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

TOOL_DESCRIPTION = """\
## Available tools

Call tools by embedding XML tags in your response:

<TOOL:Bash>shell command here</TOOL>
  Execute a shell command. Returns stdout+stderr (first 2000 chars).

<TOOL:Read>/absolute/path/to/file.py</TOOL>
  Read a file. Returns content (first 3000 chars).

<TOOL:Edit>{"file_path": "/abs/path.py", "old_string": "exact old text", "new_string": "new text"}</TOOL>
  Replace exact text in a file. old_string must be unique in the file.

<TOOL:Write>{"file_path": "/abs/path.py", "content": "full file content"}</TOOL>
  Write a complete file (creates or overwrites).

## Termination

When the ticket is fully done (code written, tests green, committed, closed), start your
final message with:
  DONE: <one-line summary of what was accomplished>

## Rules

- Always run tests after code changes: <TOOL:Bash>.venv/bin/python3 -m pytest tests/ -q --tb=short 2>&1 | tail -15</TOOL>
- Stage files specifically by name — never git add -A or git add .
- Commit message must include: Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
- Never force-push
- Use absolute paths for Read/Edit/Write
"""


class ToolLoop:
    """Multi-turn ReAct inference loop with Bash/Read/Edit/Write execution."""

    def __init__(self, max_turns: int = MAX_TURNS) -> None:
        self._max_turns = max_turns

    def run(self, ticket: dict, system_prompt: str) -> str | None:
        """Work a ticket through the tool loop.

        Returns the DONE summary text, or the last assistant message if no
        DONE signal, or None if inference failed entirely.
        """
        from devices.inference.device import InferenceDevice
        from devices.inference.shim import InferenceRequest

        ticket_id = ticket.get("id", "?")
        user_msg = (
            f"Ticket ID: {ticket_id}\n"
            f"Title: {ticket.get('title', 'No title')}\n"
            f"Tags: {', '.join(ticket.get('tags', []))}\n\n"
            f"Description:\n{ticket.get('description', ticket.get('title', ''))}"
        )
        messages = [{"role": "user", "content": user_msg}]
        full_system = system_prompt + "\n\n" + TOOL_DESCRIPTION

        for turn in range(self._max_turns):
            log.info("ToolLoop turn %d/%d — ticket %s", turn + 1, self._max_turns, ticket_id)
            req = InferenceRequest(
                model="",
                messages=messages,
                system=full_system,
                task_class="worker",
                agent_id="dicksimnel",
                max_tokens=4096,
                timeout=120,
            )
            try:
                response = InferenceDevice().dispatch(req)
            except Exception as exc:
                log.error("ToolLoop inference failed on turn %d: %s", turn + 1, exc)
                return None

            text = response.text
            log.debug("ToolLoop turn %d: %d chars", turn + 1, len(text))

            if text.lstrip().startswith(DONE_PREFIX):
                log.info("ToolLoop: DONE on turn %d for %s", turn + 1, ticket_id)
                return text

            tool_calls = _parse_tool_calls(text)
            if not tool_calls:
                log.info("ToolLoop: no tool calls on turn %d — treating as implicit DONE", turn + 1)
                return text

            results = []
            for tool_name, tool_input in tool_calls:
                result = _execute_tool(tool_name, tool_input)
                log.info("ToolLoop: %s → %d chars result", tool_name, len(result))
                results.append(f"<TOOL_RESULT:{tool_name}>\n{result}\n</TOOL_RESULT>")

            messages.append({"role": "assistant", "content": text})
            messages.append({
                "role": "user",
                "content": "\n\n".join(results) + "\n\nContinue with the next step.",
            })

        log.warning("ToolLoop: hit max turns (%d) for %s", self._max_turns, ticket_id)
        # Return the last assistant message as best-effort result
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                return msg["content"]
        return None


# ── Tool dispatch ─────────────────────────────────────────────────────────────


def _parse_tool_calls(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2).strip()) for m in _TOOL_PATTERN.finditer(text)]


def _execute_tool(name: str, input_text: str) -> str:
    try:
        if name == "Bash":
            return _tool_bash(input_text)
        if name == "Read":
            return _tool_read(input_text)
        if name == "Edit":
            return _tool_edit(input_text)
        if name == "Write":
            return _tool_write(input_text)
        return f"ERROR: unknown tool {name!r}"
    except Exception as exc:
        log.warning("ToolLoop _execute_tool %s raised: %s", name, exc)
        return f"ERROR: {exc}"


def _tool_bash(command: str) -> str:
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
    p = Path(path.strip())
    if not p.exists():
        return f"ERROR: file not found: {p}"
    try:
        content = p.read_text(errors="replace")
        return content[:3000] + ("...(truncated)" if len(content) > 3000 else "")
    except Exception as exc:
        return f"ERROR: {exc}"


def _tool_edit(input_text: str) -> str:
    try:
        params = json.loads(input_text)
    except json.JSONDecodeError as exc:
        return f"ERROR: invalid JSON for Edit: {exc}"
    file_path = params.get("file_path", "")
    old_string = params.get("old_string", "")
    new_string = params.get("new_string", "")
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
        return f"ERROR: {exc}"


def _tool_write(input_text: str) -> str:
    try:
        params = json.loads(input_text)
    except json.JSONDecodeError as exc:
        return f"ERROR: invalid JSON for Write: {exc}"
    file_path = params.get("file_path", "")
    content = params.get("content", "")
    if not file_path:
        return "ERROR: file_path required"
    try:
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"OK: wrote {len(content)} chars to {p}"
    except Exception as exc:
        return f"ERROR: {exc}"

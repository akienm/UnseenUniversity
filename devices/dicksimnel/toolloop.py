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

MAX_TURNS = 20
DONE_PREFIX = "DONE:"

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
## Rules

- Always run tests after code changes: use Bash with `.venv/bin/python3 -m pytest tests/ -q --tb=short 2>&1 | tail -15`
- Stage files specifically by name — never git add -A or git add .
- Commit message must include: Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
- Never force-push
- Use absolute paths for Read/Edit/Write

## Completion

When the ticket is fully done (code written, tests green, committed, ticket closed), respond with plain text starting with:
  DONE: <one-line summary of what was accomplished>
Do not call any more tools after the ticket is closed.
"""


class ToolLoop:
    """Multi-turn ReAct inference loop using native OR tool calling."""

    def __init__(self, max_turns: int = MAX_TURNS) -> None:
        self._max_turns = max_turns

    def run(self, ticket: dict, system_prompt: str) -> str | None:
        """Work a ticket through the tool loop.

        Returns the model's final text when it stops calling tools, or the
        last assistant content if max_turns is hit, or None if inference failed.
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
        full_system = system_prompt + "\n\n" + SYSTEM_RULES

        for turn in range(self._max_turns):
            log.info("ToolLoop turn %d/%d — ticket %s", turn + 1, self._max_turns, ticket_id)
            req = InferenceRequest(
                model="",
                messages=messages,
                system=full_system,
                tools=TOOL_DEFINITIONS,
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

            tool_calls = response.tool_calls
            log.debug(
                "ToolLoop turn %d: %d chars, %d tool calls",
                turn + 1,
                len(response.text or ""),
                len(tool_calls) if tool_calls else 0,
            )

            if not tool_calls:
                log.info("ToolLoop: done on turn %d for %s", turn + 1, ticket_id)
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
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                result = _execute_tool(name, args)
                log.info("ToolLoop: %s → %d chars result", name, len(result))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                })

        log.warning("ToolLoop: hit max turns (%d) for %s", self._max_turns, ticket_id)
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                return msg.get("content") or ""
        return None


# ── Tool dispatch ─────────────────────────────────────────────────────────────


def _execute_tool(name: str, args: dict) -> str:
    """Dispatch a tool call. args is a parsed dict from the model's tool_call."""
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
        log.warning("ToolLoop Read failed for %s: %s", p, exc)
        return f"ERROR: {exc}"


def _tool_edit(args: dict) -> str:
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

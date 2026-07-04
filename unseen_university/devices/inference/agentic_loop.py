"""
agentic_loop.py — the ONE shared ReAct tool-execution loop.

D-domain-object-encapsulation-2026-07-01 / T-domain-owns-loop-and-escalation. The two
former per-device loops (dicksimnel/toolloop.py, minion/tool_loop.py) converge here into
a single turn-runner. What differs between callers is NOT the loop — it is the tool-call
CODEC: how tools are offered to the model and how its response is parsed. Worker+ models
do native OpenAI function-calling (features=['tools']); minion-tier small models
(qwen3.5-9b, llama3.2:3b — no tools feature) can't, and use an XML text protocol. So the
codec is a pluggable strategy; the mechanism (turn loop, cost cap, flat-rate turn-raise,
availability detection, terminal handling, optional Critic) is shared.

The domain OWNS escalation policy and DRIVES this loop (BaseDomain.run); the loop itself
has NO escalation walk — it runs ONE attempt and returns a typed LoopResult. That result's
`outcome` is what the domain's money-safety policy classifies (done / cost / availability /
capability), so the availability-vs-capability split that keeps a source-down from driving
paid escalation ('Hex-DOWN is not a branch') lives at the boundary, typed, not string-sniffed.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

MAX_TURNS = 50
MAX_TURNS_FLAT_RATE = 80  # flat-rate sources don't pay per turn; give more budget
COST_CAP_USD = 3.00  # only enforced for usage_based sources

# Context discipline (T-agentic-loop-context-discipline): the loop re-sends the WHOLE
# message history every turn, so unbounded accumulation of 3000-char tool dumps makes the
# prompt grow linearly in turns until it overflows num_ctx and the small model drowns
# (2026-07-03 DS.0 observe-run: 2908 → ~32000 input_tokens over 38 turns → timeout cliff).
# We bound it by keeping only the task message + the last N complete turn-groups; older
# groups are dropped wholesale at assistant boundaries so growth is O(1) in turns.
HISTORY_WINDOW_TURNS = 10  # recent turn-groups kept in full; 0 disables compaction

# LoopResult.outcome values — the typed boundary the domain's escalation policy reads.
LOOP_DONE = "done"                # model claimed completion (done envelope)
LOOP_ESCALATE = "escalate"        # finished-but-not-done (escalate envelope or prose finish)
LOOP_COST_EXCEEDED = "cost_exceeded"  # usage_based run hit its per-run cost cap
LOOP_AVAILABILITY = "availability"    # no live source reached (down); NOT a model failure
LOOP_MAX_TURNS = "max_turns"      # hit the turn cap without a terminal
LOOP_ERROR = "error"              # unexpected loop failure

# Bash commands blocked by the safety denylist (shared across both codecs).
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
            "description": "Execute a shell command. Returns stdout+stderr (first 3000 chars).",
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
                    "path": {"type": "string", "description": "Path to file"},
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

# Native-protocol exit rules (appended to the domain's system prompt for native callers).
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


# ── Terminal parsing (shared) ────────────────────────────────────────────────


def parse_terminal_envelope(text: str) -> dict | None:
    """Parse a terminal (no-tool-calls) native response into an envelope, or None.

    Returns a dict with at least {"status": ...} for a valid terminal envelope (JSON or
    legacy DONE:/ESCALATE: prefix); None when the text is not a terminal signal (planning
    mode, etc.). Kept public — DS envelope tests and the native codec both use it.
    """
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("{"):
        try:
            envelope = json.loads(stripped)
            if isinstance(envelope, dict) and "status" in envelope:
                return envelope
        except json.JSONDecodeError:
            pass
    if stripped.startswith("DONE:"):
        return {"status": "done", "result": stripped[5:].strip(), "error_class": None, "error_number": None}
    if stripped.upper().startswith("ESCALATE:"):
        return {"status": "escalate", "result": stripped[9:].strip(), "error_class": "ESCALATE", "error_number": None}
    return None


# ── Tool dispatch (shared) ────────────────────────────────────────────────────


def execute_tool(name: str, args: dict, cwd: Path) -> str:
    """Route a tool call to its handler; return a string result for the model.

    Relative paths resolve against `cwd` (native callers pass absolute paths, so the join
    is a no-op for them; the text/minion protocol relies on it). One shared implementation.
    """
    try:
        if name == "Bash":
            return _tool_bash(args.get("command", ""), cwd)
        if name == "Read":
            return _tool_read(args.get("path", ""), cwd)
        if name == "Edit":
            return _tool_edit(args, cwd)
        if name == "Write":
            return _tool_write(args, cwd)
        return f"ERROR: unknown tool {name!r}"
    except Exception as exc:
        log.warning("agentic_loop execute_tool %s raised: %s", name, exc)
        return f"ERROR: {exc}"


def _resolve(path: str, cwd: Path) -> Path:
    p = Path(path.strip())
    return p if p.is_absolute() else cwd / p


def _tool_bash(command: str, cwd: Path) -> str:
    """Run a shell command; denylist blocks destructive patterns before subprocess is called."""
    if _BASH_DENYLIST.search(command):
        log.warning("agentic_loop Bash denylist blocked: %r", command[:80])
        return "ERROR: command blocked by safety denylist"
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=120, cwd=cwd,
        )
        out = (result.stdout + result.stderr)[:3000]
        return f"[Bash rc={result.returncode}]\n{out or '(no output)'}"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out (120s)"


def _tool_read(path: str, cwd: Path) -> str:
    """Read and return a file's content (truncated to 3000 chars to stay inside model context)."""
    p = _resolve(path, cwd)
    if not p.exists():
        return f"ERROR: file not found: {p}"
    try:
        content = p.read_text(errors="replace")
        return content[:3000] + ("...(truncated)" if len(content) > 3000 else "")
    except Exception as exc:
        log.warning("agentic_loop Read failed for %s: %s", p, exc)
        return f"ERROR: {exc}"


def _tool_edit(args: dict, cwd: Path) -> str:
    """Exact-string replacement; rejects non-unique old_string to prevent silent mass-edits."""
    file_path = args.get("file_path") or args.get("path", "")
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    if not file_path:
        return "ERROR: file_path required"
    p = _resolve(file_path, cwd)
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
        log.warning("agentic_loop Edit failed for %s: %s", file_path, exc)
        return f"ERROR: {exc}"


def _tool_write(args: dict, cwd: Path) -> str:
    """Write a complete file, creating parent dirs; overwrites any existing content."""
    file_path = args.get("file_path") or args.get("path", "")
    content = args.get("content", "")
    if not file_path:
        return "ERROR: file_path required"
    try:
        p = _resolve(file_path, cwd)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"OK: wrote {len(content)} chars to {p}"
    except Exception as exc:
        log.warning("agentic_loop Write failed for %s: %s", file_path, exc)
        return f"ERROR: {exc}"


# ── Tool-call codecs (the pluggable strategy) ─────────────────────────────────


@dataclass
class ParsedResponse:
    """One model response decoded by a codec into loop-uniform parts."""
    terminal: dict | None          # terminal envelope {status,...} or None
    tool_calls: list[dict]         # normalized [{"id","name","args"}]
    assistant_content: str         # raw text, for message history


class ToolCallCodec:
    """Strategy for offering tools to a model and parsing its response.

    Subclasses adapt the shared loop to a model family's tool-call protocol. The loop
    calls: offers_tools, request_extra(turn), parse(response), should_correct(turn),
    append_assistant/append_tool_result, and reads correction_text.
    """

    name = "base"
    offers_tools = False
    correction_text = (
        "No tool call detected. Please call a tool to take action, or signal completion."
    )

    def request_extra(self, turn: int) -> dict:
        return {}

    def parse(self, response) -> ParsedResponse:  # pragma: no cover - abstract
        raise NotImplementedError

    def should_correct(self, turn: int) -> bool:
        """Whether a turn with no terminal and no tool calls should be corrected-and-continued."""
        return True

    def append_assistant(self, messages: list, parsed: ParsedResponse) -> None:
        messages.append({"role": "assistant", "content": parsed.assistant_content or None})

    def append_tool_result(self, messages: list, call: dict, result: str) -> None:  # pragma: no cover
        raise NotImplementedError


class NativeToolCodec(ToolCallCodec):
    """Native OpenAI function-calling — worker+ models. Multiple tool calls per turn."""

    name = "native"
    offers_tools = True

    def request_extra(self, turn: int) -> dict:
        # Force tool use on turn 1 to prevent planning-mode narration.
        return {"tool_choice": "required"} if turn == 0 else {}

    def parse(self, response) -> ParsedResponse:
        raw_calls = response.tool_calls or []
        norm: list[dict] = []
        for tc in raw_calls:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            if isinstance(raw_args, dict):
                args = raw_args
            else:
                try:
                    args = json.loads(raw_args)
                except (json.JSONDecodeError, TypeError):
                    args = {}
            norm.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "args": args, "_raw": tc})
        if norm:
            return ParsedResponse(terminal=None, tool_calls=norm, assistant_content=response.text or "")
        # No tool calls → a terminal envelope, or (turn 0) planning-mode to correct.
        return ParsedResponse(
            terminal=parse_terminal_envelope(response.text or ""),
            tool_calls=[],
            assistant_content=response.text or "",
        )

    def should_correct(self, turn: int) -> bool:
        # Only turn 1 planning-mode gets a correction; a later prose finish is a real terminal.
        return turn == 0

    def append_assistant(self, messages: list, parsed: ParsedResponse) -> None:
        msg = {"role": "assistant", "content": parsed.assistant_content or None}
        if parsed.tool_calls:
            msg["tool_calls"] = [c["_raw"] for c in parsed.tool_calls]
        messages.append(msg)

    def append_tool_result(self, messages: list, call: dict, result: str) -> None:
        messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})


class TextToolCodec(ToolCallCodec):
    """XML text protocol — minion-tier small models that can't do native tool calls.

    One tool call per turn (<tool>...</tool>); DONE:/ESCALATE: signal anywhere takes
    precedence over a tool tag. Preserves the escalate TARGET (worker|analyst|designer).
    """

    name = "text"
    correction_text = (
        "No tool call detected. Please output a tool call using the format in your "
        "instructions, or signal DONE: or ESCALATE:."
    )

    def parse(self, response) -> ParsedResponse:
        text = response.text or ""
        terminal = _parse_text_signal(text)
        if terminal is not None:
            return ParsedResponse(terminal=terminal, tool_calls=[], assistant_content=text)
        action = _parse_text_tool_call(text)
        calls = [{"id": "", "name": action["tool"], "args": action}] if action else []
        return ParsedResponse(terminal=None, tool_calls=calls, assistant_content=text)

    def should_correct(self, turn: int) -> bool:
        return True  # no signal + no tool → correct every turn (loops to MAX_TURNS if stuck)

    def append_tool_result(self, messages: list, call: dict, result: str) -> None:
        messages.append({"role": "user", "content": result})


def _parse_text_tool_call(text: str) -> dict | None:
    """Extract the first XML tool call from a text response. Returns None if not found."""
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
            return {"tool": "Edit", "path": pm.group(1).strip(),
                    "old_string": om.group(1), "new_string": nm.group(1)}
    elif tool == "Write":
        pm = re.search(r"<path>(.*?)</path>", text, re.DOTALL)
        cm = re.search(r"<content>(.*?)</content>", text, re.DOTALL)
        if pm and cm:
            return {"tool": "Write", "path": pm.group(1).strip(), "content": cm.group(1)}
    return None


def _parse_text_signal(text: str) -> dict | None:
    """Parse a DONE/ESCALATE terminal signal from text → envelope (with escalate target), or None."""
    done_m = re.search(r"\bDONE:\s*(.+)", text)
    if done_m:
        return {"status": "done", "result": done_m.group(1).strip()}
    esc_m = re.search(r"\bESCALATE:\s*(worker|analyst|designer)\b", text, re.IGNORECASE)
    if esc_m:
        target = esc_m.group(1).lower()
        reason = text[esc_m.end(): esc_m.end() + 600].strip()
        return {"status": "escalate", "result": reason, "target": target}
    return None


# ── The shared loop ───────────────────────────────────────────────────────────


@dataclass
class LoopResult:
    """One agentic-loop attempt's typed outcome — what the domain's policy classifies."""
    outcome: str
    text: str = ""
    envelope: dict | None = None
    turns: int = 0
    tools_called: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def _outcome_from_envelope(env: dict | None) -> str:
    """Map a terminal envelope to a LoopResult outcome; finished-but-not-done → escalate."""
    status = (env or {}).get("status")
    if status == "done":
        return LOOP_DONE
    return LOOP_ESCALATE  # explicit escalate, prose finish, or unknown terminal


def _bash_failed(result: str) -> bool:
    """True if a Bash tool result is a failure (non-zero exit, or a denylist/timeout ERROR)."""
    m = re.search(r"\[Bash rc=(\d+)\]", result)
    if m:
        return int(m.group(1)) != 0
    return result.startswith("ERROR")


class AgenticLoop:
    """The ONE multi-turn ReAct loop. Codec-parameterized; no escalation walk of its own."""

    def __init__(
        self,
        *,
        codec: ToolCallCodec | None = None,
        max_turns: int = MAX_TURNS,
        flat_rate_max_turns: int = MAX_TURNS_FLAT_RATE,
        cost_cap_usd: float | None = COST_CAP_USD,
        critic_enabled: bool = False,
        inference_device=None,
        history_window_turns: int = HISTORY_WINDOW_TURNS,
    ) -> None:
        self._codec = codec or NativeToolCodec()
        self._max_turns = max_turns
        self._flat_rate_max_turns = flat_rate_max_turns
        self._cost_cap_usd = cost_cap_usd
        self._critic_enabled = critic_enabled
        self._inference_device = inference_device
        # Bound the re-sent history to the task + this many recent turn-groups (0 = off).
        # Tunable per model/num_ctx without a code change (same pattern as max_turns).
        self._history_window_turns = history_window_turns
        self._turn_log: list[dict] = []

    def _compact_history(self, messages: list[dict]) -> list[dict]:
        """Bound the re-sent history to the task message + the last N complete turn-groups.

        A *group* = one assistant message plus every following non-assistant message (its
        tool results, or a correction) up to the next assistant. Because every turn starts
        by appending an assistant message, assistant messages are the only group boundaries
        and ``messages[0]`` (the task) is the sole non-group message. We drop whole groups
        only from the front and only at those boundaries, so the retained sequence is always
        ``messages[0]`` + zero-or-more COMPLETE groups. That preserves the two invariants a
        real endpoint 400s on: no orphaned tool result as the first post-task message, and no
        dangling assistant ``tool_calls`` whose results were dropped. Keyed on the assistant
        role — NOT on tool-result shape — so it is codec-agnostic (native emits ``role:tool``
        results, the text codec emits ``role:user``; both are handled identically here).

        The most-recent group is never dropped (it is what the model must act on next).
        """
        window = self._history_window_turns
        if window <= 0 or len(messages) <= 1:
            return messages
        starts = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
        if len(starts) <= window:
            return messages
        cutoff = starts[len(starts) - window]  # start index of the oldest KEPT group
        log.info("AgenticLoop: context-discipline — dropped %d old turn-group(s), keeping "
                 "task + last %d (history %d→%d msgs)",
                 len(starts) - window, window, len(messages), 1 + len(messages) - cutoff)
        return [messages[0]] + messages[cutoff:]

    def run(
        self,
        *,
        system_prompt: str,
        initial_message: str,
        task_class: str = "worker",
        domain: str = "",
        ticket_id: str = "?",
        agent_id: str = "",
        session_id: str = "",
        escalation_hop: int = 0,
        prior_attempt: str = "",
        foreground: bool = False,
        cwd: Path | None = None,
    ) -> LoopResult:
        """Run one attempt. Returns a typed LoopResult; never raises for a source-down.

        A dispatch that raises or a router with no live source both yield LOOP_AVAILABILITY
        — the signal the domain's policy re-selects on (same difficulty), never a paid bump.
        """
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        codec = self._codec
        self._turn_log = []
        cwd = cwd or _REPO_ROOT
        inference_device = self._inference_device or InferenceDevice()

        critic, critic_judgments = self._init_critic(ticket_id)

        messages = [{"role": "user", "content": initial_message}]
        full_system = system_prompt + ("\n\n" + SYSTEM_RULES if codec.offers_tools else "")
        tools = TOOL_DEFINITIONS if codec.offers_tools else None

        running_cost = 0.0
        in_tok = out_tok = 0
        tools_called: list[str] = []
        consecutive_bash_failures = 0
        source_billing_type = "usage_based"
        effective_max_turns = self._max_turns
        turn = 0

        while turn < effective_max_turns:
            # Context discipline: bound the history we re-send so token growth is O(1) in
            # turns, not linear — the whole message list rides in every request.
            messages = self._compact_history(messages)
            log.info("AgenticLoop turn %d/%d $%.4f — ticket %s (%s)",
                     turn + 1, effective_max_turns, running_cost, ticket_id, codec.name)
            req = InferenceRequest(
                messages=messages,
                system=full_system,
                tools=tools,
                task_class=task_class,
                domain=domain,
                ticket_id=ticket_id,
                agent_id=agent_id,
                session_id=session_id,
                max_tokens=4096,
                timeout=120,
                temperature=0.0,
                foreground=foreground,
                extra=codec.request_extra(turn),
                # Escalation params thread on turn 0 only — after that the live transcript is the context.
                escalation_hop=escalation_hop if turn == 0 else 0,
                prior_attempt=prior_attempt if turn == 0 else "",
            )
            try:
                response = inference_device.dispatch(req)
            except Exception as exc:
                # A raise = a source went down mid-call → AVAILABILITY, not capability.
                log.error("AgenticLoop dispatch raised turn %d for %s: %s", turn + 1, ticket_id, exc)
                self._finalize_critic(critic, critic_judgments, ticket_id)
                return LoopResult(LOOP_AVAILABILITY, text=str(exc), turns=turn,
                                  tools_called=tools_called, input_tokens=in_tok,
                                  output_tokens=out_tok, cost_usd=running_cost)
            # No live source: router returned a clean error response (finish=error / kind=none).
            if response.finish_reason == "error" or response.source_kind == "none":
                log.warning("AgenticLoop turn %d: no live source (finish=%s kind=%s) for %s — availability",
                            turn + 1, response.finish_reason, response.source_kind, ticket_id)
                self._finalize_critic(critic, critic_judgments, ticket_id)
                return LoopResult(LOOP_AVAILABILITY, text=response.text or "", turns=turn,
                                  tools_called=tools_called, input_tokens=in_tok,
                                  output_tokens=out_tok, cost_usd=running_cost)

            # Lock billing type + raise the turn cap for flat-rate sources on the first response.
            if turn == 0 and response.source_billing_type == "flat_rate":
                source_billing_type = "flat_rate"
                effective_max_turns = max(effective_max_turns, self._flat_rate_max_turns)
                log.info("AgenticLoop: flat_rate source — turn cap → %d for %s",
                         effective_max_turns, ticket_id)

            in_tok += response.input_tokens
            out_tok += response.output_tokens
            if source_billing_type != "flat_rate":
                running_cost += response.cost_estimate
                if self._cost_cap_usd is not None and running_cost >= self._cost_cap_usd:
                    log.warning("AgenticLoop: cost cap $%.4f/$%.2f turn %d for %s",
                                running_cost, self._cost_cap_usd, turn + 1, ticket_id)
                    self._finalize_critic(critic, critic_judgments, ticket_id)
                    return LoopResult(
                        LOOP_COST_EXCEEDED,
                        text=f"COST_EXCEEDED: ${running_cost:.2f} of ${self._cost_cap_usd:.2f} cap",
                        turns=turn + 1, tools_called=tools_called, input_tokens=in_tok,
                        output_tokens=out_tok, cost_usd=running_cost,
                    )

            parsed = codec.parse(response)
            self._turn_log.append({
                "turn": turn + 1,
                "had_tool_calls": bool(parsed.tool_calls),
                "tool_names": [c["name"] for c in parsed.tool_calls],
                "cost": response.cost_estimate,
            })

            # Terminal envelope → done / escalate.
            if parsed.terminal is not None and not parsed.tool_calls:
                log.info("AgenticLoop: terminal for %s turn %d status=%s",
                         ticket_id, turn + 1, parsed.terminal.get("status"))
                self._finalize_critic(critic, critic_judgments, ticket_id)
                return LoopResult(
                    _outcome_from_envelope(parsed.terminal), text=response.text or "",
                    envelope=parsed.terminal, turns=turn + 1, tools_called=tools_called,
                    input_tokens=in_tok, output_tokens=out_tok, cost_usd=running_cost,
                )

            # No tool calls and no terminal.
            if not parsed.tool_calls:
                if codec.should_correct(turn):
                    log.warning("AgenticLoop: turn %d no tool/terminal for %s — correcting",
                                turn + 1, ticket_id)
                    codec.append_assistant(messages, parsed)
                    messages.append({"role": "user", "content": codec.correction_text})
                    turn += 1
                    continue
                # Native, past turn 0: a prose finish is a real (finished-but-not-done) terminal.
                log.info("AgenticLoop: prose finish for %s turn %d — escalate", ticket_id, turn + 1)
                self._finalize_critic(critic, critic_judgments, ticket_id)
                return LoopResult(
                    LOOP_ESCALATE, text=response.text or "", turns=turn + 1,
                    tools_called=tools_called, input_tokens=in_tok,
                    output_tokens=out_tok, cost_usd=running_cost,
                )

            # Execute tool calls; append in the codec's message shape.
            codec.append_assistant(messages, parsed)
            for call in parsed.tool_calls:
                self._critic_advise(critic, call, turn, ticket_id)
                result = execute_tool(call["name"], call["args"], cwd)
                tools_called.append(call["name"])
                # Soft escalation nudge: after 3 consecutive failed Bash calls, hint the model
                # to escalate rather than grind (preserved from minion's loop; harmless + useful
                # for any caller, native or text).
                if call["name"] == "Bash":
                    if _bash_failed(result):
                        consecutive_bash_failures += 1
                        if consecutive_bash_failures >= 3:
                            result += "\n[HINT: 3 consecutive non-zero exits — consider escalating if stuck]"
                    else:
                        consecutive_bash_failures = 0
                log.info("AgenticLoop: %s → %d chars result", call["name"], len(result))
                self._critic_evaluate(critic, critic_judgments, call, result, turn, ticket_id)
                codec.append_tool_result(messages, call, result)
            turn += 1

        log.warning("AgenticLoop: hit max turns (%d) for %s", effective_max_turns, ticket_id)
        self._finalize_critic(critic, critic_judgments, ticket_id)
        return LoopResult(
            LOOP_MAX_TURNS,
            text=f"MAX_TURNS: hit {effective_max_turns} turns without terminal response",
            turns=effective_max_turns, tools_called=tools_called, input_tokens=in_tok,
            output_tokens=out_tok, cost_usd=running_cost,
        )

    # ── Critic (advisory, DS/coding-only; fail-open) ──────────────────────────

    def _init_critic(self, ticket_id: str):
        if not self._critic_enabled:
            return None, []
        try:
            from unseen_university.devices.critic.device import CriticDevice
            critic = CriticDevice()
            log.info("AgenticLoop: Critic initialized for %s (%d prior rules)",
                     ticket_id, len(critic._agent.export_rules()))
            return critic, []
        except Exception as exc:
            log.debug("AgenticLoop: critic init skipped (non-fatal): %s", exc)
            return None, []

    def _critic_advise(self, critic, call: dict, turn: int, ticket_id: str) -> None:
        if critic is None:
            return
        try:
            rec = critic.get_recommendation(
                {"decision_point": "tool_selection", "tool_name": call["name"], "turn": turn + 1}
            )
            if rec:
                log.info("AgenticLoop: Critic advisory|ticket=%s|turn=%d|rule=%s|action=%s",
                         ticket_id, turn + 1, rec["rule"], rec["action"][:80])
        except Exception:
            pass

    def _critic_evaluate(self, critic, judgments: list, call: dict, result: str, turn: int, ticket_id: str) -> None:
        if critic is None:
            return
        try:
            from unseen_university.devices.critic.agent import Decision as CriticDecision
            decision = CriticDecision(
                ticket_id=ticket_id, turn_num=turn + 1, decision_point="tool_selection",
                choice=call["name"],
                context={"ticket_id": ticket_id, "turn": turn + 1, "tool_args": call["args"]},
                tool_result=result[:200],
            )
            judgments.append(critic._agent.evaluate_decision(decision))
        except Exception:
            pass

    def _finalize_critic(self, critic, judgments: list, ticket_id: str) -> None:
        if critic is None or not judgments:
            return
        try:
            analysis = critic._agent.analyze_pattern(judgments)
            critic.learn_from_critic(analysis)
            log.info("AgenticLoop: critic_finalize|ticket=%s|judgments=%d|rules_saved=%d",
                     ticket_id, len(judgments), critic._agent.get_stats()["rules_learned"])
        except Exception as exc:
            log.debug("AgenticLoop: critic_finalize failed (non-fatal): %s", exc)

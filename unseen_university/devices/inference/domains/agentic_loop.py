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

# Per-request inference wall-clock. The base is a usage-based safety cliff; flat-rate/local
# sources (Hex/ollama, $0) don't pay per second, so a longer wall is FREE — a slow local 24b
# generating a full plan needs more than 120s (2026-07-04 confirm-by: architect timed out at
# 121s on turn 12, before producing a plan). Raised for flat-rate exactly as MAX_TURNS is
# (T-ds-hex-dispatch-timeout-midloop); same turn-0 billing-lock pattern.
INFERENCE_TIMEOUT = 120
INFERENCE_TIMEOUT_FLAT_RATE = 600

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
LOOP_NO_CAPABLE_MODEL = "no_capable_model"  # routed capability ceiling — CAPABILITY (escalate),
#                                             the CP3 signal that used to be laundered into
#                                             LOOP_AVAILABILITY (T-inference-typed-no-path-result)


def no_source_loop_outcome(response) -> str:
    """Map a no-live-source dispatch response to the LoopResult outcome the walk reads.

    Every loop impl catches a no-source response the same way (finish_reason=='error' or the
    reliable source_kind=='none'); this is the ONE place that decides what it MEANS. dispatch
    stamps a TYPED finish_reason: a routed capability ceiling → FINISH_NO_CAPABLE_MODEL, which
    maps to LOOP_NO_CAPABLE_MODEL → the walk ESCALATES a rung. Anything else — an availability
    outage, a dead legacy source, a mocked generic 'error' — maps to LOOP_AVAILABILITY → the
    walk RETRIES the same rung. Not laundering the first into the second is the whole CP3 fix
    (T-inference-typed-no-path-result); keeping it in one helper keeps the three loops honest
    together (homogeneity over three drifting special-cases).
    """
    from unseen_university.devices.inference.shim import FINISH_NO_CAPABLE_MODEL

    if getattr(response, "finish_reason", "") == FINISH_NO_CAPABLE_MODEL:
        return LOOP_NO_CAPABLE_MODEL
    return LOOP_AVAILABILITY

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

# ACI variant (T-coding-minion-aci-edit-centric): the minion/weak tier gets a PAGED,
# line-numbered Read (100-line windows + an offset to scroll) instead of a blind whole-file
# truncation, and edit-centric guidance — SWE-agent's finding that a tuned interface beats a
# naive tool loop for the SAME model. Default callers keep TOOL_DEFINITIONS (whole-file, rich).
_ACI_READ_WINDOW = 100
_ACI_READ_DEF = {
    "type": "function",
    "function": {
        "name": "Read",
        "description": (
            "Read a 100-line window of a file (line-numbered). Pass `offset` to scroll to a "
            "later line. Read only enough to locate the change, then make it with Edit/Write."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to file"},
                "offset": {"type": "integer", "description": "0-based line to start the window at"},
            },
            "required": ["path"],
        },
    },
}
ACI_TOOL_DEFINITIONS = [_ACI_READ_DEF] + [t for t in TOOL_DEFINITIONS if t["function"]["name"] != "Read"]

# Architect (plan_mode) Read: the read-only PLANNER must read whole files to write a precise
# plan. The windowed ACI Read (designed for the EDITOR applying narrow edits) cripples it — it
# pages through every file 100 lines at a time and burns its whole turn budget before reaching a
# plan (observed in the inference I/O corpus, 2026-07-05). On Hex a large read is free, so the
# architect gets the WHOLE file, line-numbered, up to a generous cap. No `offset` — one call reads
# the file.
_FULL_READ_MAX_LINES = 1500
_FULL_READ_DEF = {
    "type": "function",
    "function": {
        "name": "Read",
        "description": (
            "Read an entire file (line-numbered). Read the files you need to understand the "
            "change, then write the plan. Do not run the test suite — just read and plan."
        ),
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to file"}},
            "required": ["path"],
        },
    },
}


def _select_tool_defs(aci_mode: bool, plan_mode: bool, tool_names: list[str] | None) -> list[dict]:
    """Choose the offered tool definitions for one loop.

    Pure (no self) so it is unit-testable without a mock dispatch. `aci_mode` picks the windowed
    minion Read; `plan_mode` (the read-only architect) overrides Read with the full-file def so
    the planner reads whole files; `tool_names`, when set, filters the offer to those names
    (the architect is filtered to Read/Bash — the edit ban is structural, not advisory).
    """
    base = ACI_TOOL_DEFINITIONS if aci_mode else TOOL_DEFINITIONS
    if plan_mode:
        base = [_FULL_READ_DEF] + [t for t in base if t["function"]["name"] != "Read"]
    if tool_names is not None:
        base = [t for t in base if t["function"]["name"] in tool_names]
    return base


# The read-only architect must not spend its turn/timeout budget running the whole test suite
# mid-plan (observed in the corpus). Deflect a broad pytest run — `pytest` NOT followed by a
# `.py` file/node on the same line, i.e. a whole-directory or bare-suite run — while allowing a
# targeted single-file run through. The editor runs the tests the plan names.
_BROAD_PYTEST_RE = re.compile(r"\bpytest\b(?![^\n]*\.py)", re.IGNORECASE)

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


def execute_tool(name: str, args: dict, cwd: Path, aci_mode: bool = False, plan_mode: bool = False) -> str:
    """Route a tool call to its handler; return a string result for the model.

    Relative paths resolve against `cwd` (native callers pass absolute paths, so the join
    is a no-op for them; the text/minion protocol relies on it). One shared implementation.
    `aci_mode` switches Read to the paged, line-numbered window (minion tier); default off.
    `plan_mode` (the read-only architect) reads whole files and deflects broad test-suite runs.
    """
    try:
        if name == "Bash":
            return _tool_bash(args.get("command", ""), cwd, plan_mode=plan_mode)
        if name == "Read":
            return _tool_read(args.get("path", ""), cwd, aci_mode=aci_mode,
                              offset=args.get("offset", 0), full_read=plan_mode)
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


def _tool_bash(command: str, cwd: Path, plan_mode: bool = False) -> str:
    """Run a shell command; denylist blocks destructive patterns before subprocess is called.

    `plan_mode` (architect): deflect a broad test-suite run — the planner reads code and writes a
    plan; the editor runs the tests the plan names. A targeted single-file run is still allowed.
    """
    if plan_mode and _BROAD_PYTEST_RE.search(command):
        log.info("agentic_loop Bash: deflected broad pytest in plan_mode: %r", command[:80])
        return ("[planning] Skip running the test suite while planning — read the code and write "
                "the plan. The editor will run the tests you name in the plan.")
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


def _tool_read(path: str, cwd: Path, aci_mode: bool = False, offset: int = 0, full_read: bool = False) -> str:
    """Read a file for the model.

    `full_read` (architect/plan_mode): the WHOLE file, line-numbered, up to `_FULL_READ_MAX_LINES`
    — the read-only planner must see whole files to plan, and a large read is free on Hex. Checked
    FIRST, because the architect runs with aci_mode AND full_read both true and must NOT fall
    through to the windowed branch. Default (rich tier): whole-file content truncated to 3000
    chars. ACI/minion mode: a paged, line-numbered 100-line window starting at `offset`, with a
    header naming the range and how to scroll — so a weak model reads coherent slices it controls
    instead of a blind truncation (T-coding-minion-aci-edit-centric).
    """
    p = _resolve(path, cwd)
    if not p.exists():
        return f"ERROR: file not found: {p}"
    try:
        content = p.read_text(errors="replace")
    except Exception as exc:
        log.warning("agentic_loop Read failed for %s: %s", p, exc)
        return f"ERROR: {exc}"
    if full_read:
        lines = content.splitlines()
        total = len(lines)
        shown = lines[:_FULL_READ_MAX_LINES]
        numbered = "\n".join(f"{i + 1:>5}| {ln}" for i, ln in enumerate(shown))
        if total > _FULL_READ_MAX_LINES:
            tail = (f"\n[file truncated at {_FULL_READ_MAX_LINES} of {total} lines — "
                    f"Bash `sed -n '{_FULL_READ_MAX_LINES + 1},$p' {p}` for the rest]")
            return f"[{p.name} lines 1-{_FULL_READ_MAX_LINES} of {total}]\n{numbered}{tail}"
        return f"[{p.name} lines 1-{total} of {total}]\n{numbered}"
    if not aci_mode:
        return content[:3000] + ("...(truncated)" if len(content) > 3000 else "")
    lines = content.splitlines()
    total = len(lines)
    start = max(0, int(offset or 0))
    window = lines[start:start + _ACI_READ_WINDOW]
    end = start + len(window)
    numbered = "\n".join(f"{start + i + 1:>5}| {ln}" for i, ln in enumerate(window))
    more = (f" — call Read offset={end} for the next {_ACI_READ_WINDOW} lines"
            if end < total else " — end of file")
    return f"[{p.name} lines {start + 1}-{end} of {total}{more}]\n{numbered}"


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
    #: WHICH model/source actually served this attempt. The escalation walk resolves a tier, not a
    #: model, so without these the run record cannot answer the first question a reader asks —
    #: "who answered?" — and the glance-view would be useless without joining the io corpus.
    #: Empty when the attempt never reached a source (availability), which is itself the signal.
    model: str = ""
    source_kind: str = ""


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
        inference_timeout: int = INFERENCE_TIMEOUT,
        flat_rate_timeout: int = INFERENCE_TIMEOUT_FLAT_RATE,
        cost_cap_usd: float | None = COST_CAP_USD,
        critic_enabled: bool = False,
        inference_device=None,
        history_window_turns: int = HISTORY_WINDOW_TURNS,
        tool_names: list[str] | None = None,
        aci_mode: bool = False,
        plan_mode: bool = False,
    ) -> None:
        self._codec = codec or NativeToolCodec()
        self._max_turns = max_turns
        self._flat_rate_max_turns = flat_rate_max_turns
        self._inference_timeout = inference_timeout
        self._flat_rate_timeout = flat_rate_timeout
        self._cost_cap_usd = cost_cap_usd
        self._critic_enabled = critic_enabled
        self._inference_device = inference_device
        # Restrict the offered tool set to these names (None = all TOOL_DEFINITIONS). The
        # architect/editor split (D-coding-loop-redesign) uses this to give the planner
        # Read/Bash but NOT Edit/Write, so it cannot wander into editing — its one job is
        # to emit a plan. Filtering the offer (not just the prompt) makes the constraint
        # structural, not advisory.
        self._tool_names = tool_names
        # Minion-tier ACI (T-coding-minion-aci-edit-centric): paged windowed Read + edit-centric
        # tool guidance. Off = the rich whole-file tools (strong tier keeps them unchanged).
        self._aci_mode = aci_mode
        # Architect/planner mode (T-architect-read-window-unblock): whole-file Read + broad-pytest
        # deflection, so the read-only planner reads whole files and doesn't burn its budget on the
        # windowed pager or the full suite. Off = the editor/normal path (windowed reads unchanged).
        self._plan_mode = plan_mode
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
        role: str = "",
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
        tool_defs = _select_tool_defs(self._aci_mode, self._plan_mode, self._tool_names)
        tools = tool_defs if codec.offers_tools else None

        running_cost = 0.0
        in_tok = out_tok = 0
        tools_called: list[str] = []
        consecutive_bash_failures = 0
        source_billing_type = "usage_based"
        effective_max_turns = self._max_turns
        effective_timeout = self._inference_timeout
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
                timeout=effective_timeout,
                temperature=0.0,
                foreground=foreground,
                extra=codec.request_extra(turn),
                # Escalation params thread on turn 0 only — after that the live transcript is the context.
                escalation_hop=escalation_hop if turn == 0 else 0,
                prior_attempt=prior_attempt if turn == 0 else "",
                # This loop OWNS the no-path outcome (it runs the escalation walk) — dispatch
                # suppresses its chokepoint alarm so exactly one mouth sounds per walk, at the
                # walk's terminal (T-inference-typed-no-path-result).
                escalation_driven=True,
                # Layer labels for corpus segmentation (T-corpus-visibility-gaps): which loop
                # role this call plays and which turn it is within the attempt.
                role=role,
                turn=turn,
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
            # No live source: router returned a clean error response (typed finish_reason /
            # kind=none). The TYPE decides capability-ceiling (escalate) vs availability
            # (retry) — no longer laundered to AVAILABILITY wholesale (the CP3 bug).
            if response.finish_reason == "error" or response.source_kind == "none":
                outcome = no_source_loop_outcome(response)
                log.warning("AgenticLoop turn %d: no live source (finish=%s kind=%s) for %s — %s",
                            turn + 1, response.finish_reason, response.source_kind, ticket_id, outcome)
                self._finalize_critic(critic, critic_judgments, ticket_id)
                return LoopResult(outcome, text=response.text or "", turns=turn,
                                  tools_called=tools_called, input_tokens=in_tok,
                                  output_tokens=out_tok, cost_usd=running_cost)

            # Lock billing type + raise the turn cap AND the per-request timeout for any
            # FREE-WALL source on the first response: a flat-rate subscription OR an on-box
            # LOCAL box (Hex, $0) — neither pays per second, so a longer wall is free. This is
            # what lets a slow local 24b finish a plan instead of dying at the 120s usage-based
            # cliff (T-ds-hex-dispatch-timeout-midloop). NB the on-box Ollama/Hex source reports
            # source_kind='local' with billing_type='usage_based' (cost_class='owned_local'),
            # NOT flat_rate — so keying on flat_rate alone silently missed the very source the
            # raise exists for (the 2026-07-04 funnel: every architect timed out at 120s on the
            # plan turn). source_kind=='local' is the reliable free-wall signal.
            if turn == 0 and (response.source_billing_type == "flat_rate"
                              or response.source_kind == "local"):
                source_billing_type = "flat_rate"
                effective_max_turns = max(effective_max_turns, self._flat_rate_max_turns)
                effective_timeout = max(effective_timeout, self._flat_rate_timeout)
                log.info("AgenticLoop: free-wall source (billing=%s kind=%s) — turn cap → %d, timeout → %ds for %s",
                         response.source_billing_type, response.source_kind,
                         effective_max_turns, effective_timeout, ticket_id)

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
                result = execute_tool(call["name"], call["args"], cwd,
                                      aci_mode=self._aci_mode, plan_mode=self._plan_mode)
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

import logging

"""
inner_cc — Igor's internal code-reasoning tool (D269).

A lightweight call to a capable model, scoped entirely to programming and
architecture reasoning. Produces a structured answer AND deposits graph nodes
so the knowledge stays local next time.

Design principles:
  - Smallest possible prompt/response blocks (role="extraction" class)
  - Every call deposits; the call pays for itself in reduced future calls
  - Covers Python, bash, DOS batch, PowerShell — all of Akien's scripting envs
  - Knows TheIgors codebase conventions (inertia, tier ladder, memory types)

Usage (Igor tool call):
  inner_cc(question="what is the inertia principle?")
  inner_cc(question="review this function", context="def foo(): ...", mode="code_review")
  inner_cc(question="analyze turn traces for gaps", mode="curriculum", long_running=True)

Modes:
  "architecture" — design decisions, tradeoffs, how-must-this-work
  "code_review"  — read a snippet and find issues / improvements
  "pattern"      — extract a general pattern from a specific example
  "debug"        — reason about why something is broken
  "curriculum"   — step through a training topic and deposit nodes (batch mode)

Long-running mode (D269):
  long_running=True routes to call_inner_cc_long() — a multi-turn conversation
  with prompt caching for anthropic/* models. Use for training passes, deep
  curriculum runs, and any task requiring more than one exchange.
"""

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Optional

from .registry import Tool, registry

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/akienm/TheIgors"

# Model constants — env-var overridable
# D327: model constants from inference layer
from ..cognition.inference_openrouter import OR_CHEAP_MODEL, OR_INTERACTIVE_MODEL

_DEFAULT_MODEL = os.getenv("INNER_CC_MODEL", OR_CHEAP_MODEL)
_HAIKU_MODEL = os.getenv("INNER_CC_HAIKU_MODEL", OR_INTERACTIVE_MODEL)
_SONNET_MODEL = os.getenv("INNER_CC_SONNET_MODEL", OR_INTERACTIVE_MODEL)

# ── System prompt — code-focused extraction ──────────────────────────────────
# Small enough for a 7B to follow, structured enough for quality output.
# Injects TheIgors codebase knowledge so Igor can reason about his own code.

_SYSTEM_PROMPT = """\
You are Igor's inner code-reasoning module.

CORE PATTERNS (always active):
  CP1: I don't know — never confabulate; say what you don't know
  CP2: FAIL = Further Advance In Learning — bugs are data
  CP3: There's always a why — reason from cause, not symptom
  CP4: Make everything suck less for everybody — simplicity wins
  CP6: The world is not a safe place — build safety in, don't assume it

THEIGORS CODEBASE CONVENTIONS:
  Inertia levels — HIGH: brainstem/, memory/models.py, reasoners/base.py (never casually edit)
                   MEDIUM: cognition/, memory/cortex.py (discuss before editing)
                   LOW: tools/, dashboard/ (freely improvable)
  Tier ladder — tier.1=habit(no LLM) → tier.2=Ollama(local) → tier.3=OR-cheap →
                tier.3.5=OR-interactive → tier.4=OR-sonnet → tier.5=inhibited
  Memory types — ROOT, CORE_PATTERN, IDENTITY, PROCEDURAL, INTERPRETIVE, FACTUAL, EPISODIC
  Source/runtime split — ~/TheIgors/ = source code; ~/.TheIgors/ = runtime data (never confuse)
  LLMs are graph trainers — every call should deposit nodes so next time stays local

LANGUAGES IN SCOPE: Python, bash, DOS batch (.bat/.cmd), PowerShell (.ps1)

RESPONSE FORMAT — output ONLY this JSON, no markdown, no extra text:
{
  "answer": "direct answer to the question",
  "nodes": [
    {
      "type": "procedural|factual|interpretive",
      "narrative": "1-2 sentences: the generalizable pattern",
      "confidence": 0.0-1.0,
      "parent_cp": "CP1-CP6 or empty",
      "trigger": "2-8 words that fire this (procedural only)"
    }
  ],
  "follow_up": "optional: next question to ask to go deeper, or empty string"
}

nodes: 1-3 max. Only include nodes that are general and reusable — skip session-specific detail.
If nothing generalizable, nodes=[].
"""

# ── Curriculum for programming/architecture training ─────────────────────────
# Ordered: foundation → specific. Each question is one inner_cc call.
# Run claudecode/run_inner_cc_curriculum.py to execute the full sequence.

CURRICULUM = [
    # ── TheIgors architecture foundation ─────────────────────────────────
    (
        "architecture",
        "What is the inertia principle in TheIgors and why does it exist?",
    ),
    ("architecture", "How does the tier ladder decide which model handles a turn?"),
    (
        "architecture",
        "What is the difference between tree traversal and executive search in Igor's cognition?",
    ),
    ("architecture", "Why is cloud inference reserved only for human interface turns?"),
    ("architecture", "What does 'LLMs are graph trainers' mean architecturally?"),
    (
        "architecture",
        "How do PROCEDURAL, FACTUAL, and INTERPRETIVE nodes differ in purpose?",
    ),
    (
        "architecture",
        "What is the source/runtime split and why is it critical not to confuse them?",
    ),
    (
        "architecture",
        "How does the word graph unify parsing and generation on the same weights?",
    ),
    # ── Python patterns used in this codebase ────────────────────────────
    (
        "pattern",
        "What is the pattern: register a tool with a Tool() object and registry.register()?",
    ),
    (
        "pattern",
        "What is the daemon-thread fire-and-forget pattern used in G53 habit extraction?",
    ),
    (
        "pattern",
        "What is the word graph SQLite WAL mode pattern and when should it be used?",
    ),
    (
        "pattern",
        "What is the pattern for graceful degradation: try cloud, fallback to local, fallback to apology?",
    ),
    (
        "pattern",
        "What is the SHA-256 cache-key pattern used in system_prompt.py and when is it appropriate?",
    ),
    (
        "pattern",
        "What is the dataclass + Postgres serialization pattern used in memory/models.py?",
    ),
    ("code_review", "What makes a good seed_*.py script in the claudecode/ directory?"),
    # ── Shell and cross-platform scripting ───────────────────────────────
    (
        "pattern",
        "What are the key differences between bash, DOS batch, and PowerShell for variable assignment and conditionals?",
    ),
    (
        "pattern",
        "In bash, what is the pattern for safely sourcing a .env file without exporting credentials to child processes?",
    ),
    (
        "pattern",
        "What is the nohup + background process pattern in bash, and when does it fail?",
    ),
    (
        "pattern",
        "In PowerShell, what is the equivalent of bash's set -e (exit on error)?",
    ),
    (
        "pattern",
        "What is the DOS batch pattern for checking if a file exists and branching?",
    ),
    # ── Architecture heuristics (Akien's navigational fingerprint) ───────
    (
        "architecture",
        "Apply 'How must this work?' to the question: how must Igor route a turn when OpenRouter is out of budget?",
    ),
    (
        "architecture",
        "Apply 'Where is the lever?' to reducing Igor's cloud inference costs.",
    ),
    (
        "architecture",
        "Apply 'How will us monkeys screw that up?' to Igor's self-edit capability.",
    ),
    (
        "architecture",
        "Apply 'What looks like it would fit there?' to the gap between word-graph and concept-tree layers.",
    ),
    # ── Reasoning about Igor's own code ──────────────────────────────────
    (
        "architecture",
        "When should Igor delegate codebase reasoning to Claude Code (cc.sh) instead of using an OpenRouter cloud turn?",
    ),
    (
        "architecture",
        "What is the inertia principle and how does it determine whether Igor should self-edit a file or escalate to Claude Code?",
    ),
    (
        "pattern",
        "What is the pattern for Igor to self-edit source files: read_source_file → patch_source_file → run_syntax_check → auto-commit?",
    ),
    (
        "pattern",
        "What is the pattern for Igor to spawn Claude Code for an implementation task: write the brief to workspace/, then call cc.sh?",
    ),
    (
        "architecture",
        "Why does Claude Code have a cost advantage over OpenRouter when reasoning about Igor's codebase, and when does that matter?",
    ),
    (
        "architecture",
        "How should Igor decide whether a code change is within his own edit capability vs. needs Claude Code as the implementation worker?",
    ),
    (
        "code_review",
        "When Igor reads his own source to understand a bug, what is the right sequence of tool calls to stay within budget?",
    ),
    (
        "architecture",
        "What architectural patterns in TheIgors make the system self-describing — i.e., what files should Igor read first when starting a codebase reasoning task?",
    ),
]


# ── HTTP helper with Anthropic prompt caching ────────────────────────────────


def _make_or_request(
    messages: list,
    model: str,
    max_tokens: int = 400,
    temperature: float = 0.2,
    timeout: int = 30,
) -> dict:
    """
    Execute one OpenRouter API call. For anthropic/* models, formats the system
    message as a cacheable content block (D269: OR prompt caching support).

    Returns the raw OR response dict. Raises on HTTP/network error.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERER,
    }

    # D269: Anthropic prompt caching via OR.
    # cache_control on system content block caches the stable system prompt
    # across turns; each subsequent turn only pays for delta tokens.
    out_messages = []
    if model.startswith("anthropic/"):
        headers["anthropic-beta"] = "prompt-caching-2024-07-31"
        for msg in messages:
            if msg["role"] == "system" and isinstance(msg.get("content"), str):
                out_messages.append(
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": msg["content"],
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                )
            else:
                out_messages.append(msg)
    else:
        out_messages = messages

    payload = json.dumps(
        {
            "model": model,
            "messages": out_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    ).encode()

    req = urllib.request.Request(
        f"{OPENROUTER_BASE}/chat/completions",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as http_err:
        body = ""
        try:
            body = http_err.read().decode(errors="replace")[:400]
        except Exception as _exc:
            from ..cognition.forensic_logger import log_error as _le
            _le(kind="SILENT_EXCEPT", detail=f"inner_cc.py:301: {_exc}")
        raise RuntimeError(f"HTTP {http_err.code} from OR: {body}") from http_err


# ── Single-shot call (original behaviour, unchanged) ─────────────────────────


def call_inner_cc(
    question: str,
    context: str = "",
    mode: str = "architecture",
    cortex=None,
    model: str = _DEFAULT_MODEL,
    long_running: bool = False,
) -> dict:
    """
    Make one inner_cc call. Returns parsed JSON dict or error dict.
    Deposits nodes to cortex if provided.

    long_running=True: delegates to call_inner_cc_long() for multi-turn
    conversation with prompt caching (use for training passes, curriculum runs).
    """
    if long_running:
        task = f"MODE: {mode}\n\nQUESTION: {question}"
        return call_inner_cc_long(
            task=task, context=context, model=model, cortex=cortex
        )

    user_content = f"MODE: {mode}\n\nQUESTION: {question}"
    if context:
        user_content += f"\n\nCONTEXT:\n{context[:1200]}"

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        data = _make_or_request(messages, model, max_tokens=400, temperature=0.2)
        raw = data["choices"][0]["message"]["content"].strip()
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "answer": raw if "raw" in dir() else "parse error",
            "nodes": [],
            "follow_up": "",
        }
    except Exception as e:
        _log_error(kind="INNER_CC_FAIL", detail=str(e))
        return {"answer": f"inner_cc error: {e}", "nodes": [], "follow_up": ""}

    if cortex is not None:
        _deposit_nodes(result.get("nodes", []), cortex, question)

    return result


# ── Multi-turn session (D269: long_running path) ──────────────────────────────


def call_inner_cc_long(
    task: str,
    context: str = "",
    model: str = _HAIKU_MODEL,
    cortex=None,
    max_turns: int = 12,
) -> dict:
    """
    Multi-turn inner CC session with prompt caching for anthropic/* models.

    The system prompt is cached after the first turn — subsequent turns only
    pay for delta tokens. Use for training passes, deep curriculum runs, and
    any task requiring multi-step reasoning across exchanges.

    Returns same dict format as call_inner_cc: {answer, nodes, follow_up}.
    """
    user_content = task
    if context:
        user_content += f"\n\nCONTEXT:\n{context[:3000]}"

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    final_result: dict = {"answer": "", "nodes": [], "follow_up": ""}

    for turn in range(max_turns):
        try:
            data = _make_or_request(messages, model, max_tokens=800, temperature=0.2)
            raw = (data["choices"][0]["message"].get("content") or "").strip()
            if not raw:
                # OR returned null/empty content (e.g. finish_reason=tool_calls)
                finish = data["choices"][0].get("finish_reason", "unknown")
                _log_error(
                    kind="INNER_CC_FAIL",
                    detail=f"empty content at turn {turn}, finish_reason={finish}",
                )
                break
            messages.append({"role": "assistant", "content": raw})

            # If the response is valid JSON with an "answer" key, treat as final.
            try:
                result = json.loads(raw)
                if "answer" in result:
                    final_result = result
                    break
            except json.JSONDecodeError:
                # Not a terminal JSON response — continue the conversation.
                if turn == max_turns - 1:
                    # Last turn: wrap the plain-text response.
                    final_result = {"answer": raw, "nodes": [], "follow_up": ""}

        except Exception as _e:
            _log_error(kind="INNER_CC_FAIL", detail=f"long turn {turn}: {_e}")
            final_result = {
                "answer": f"inner_cc_long error at turn {turn}: {_e}",
                "nodes": [],
                "follow_up": "",
            }
            break

    if cortex is not None and final_result.get("nodes"):
        _deposit_nodes(final_result["nodes"], cortex, task[:120])

    return final_result


# ── Node deposit ──────────────────────────────────────────────────────────────


def _deposit_nodes(nodes: list, cortex, question: str) -> int:
    """Deposit extracted nodes to cortex. Returns count deposited."""
    from ..memory.models import Memory, MemoryType

    deposited = 0
    for node in nodes:
        try:
            ntype = node.get("type", "factual").strip().lower()
            narrative = node.get("narrative", "").strip()
            confidence = float(node.get("confidence", 0.6))
            parent_cp = node.get("parent_cp", "").strip()
            trigger = node.get("trigger", "").strip()

            if not narrative or confidence < 0.55:
                continue

            mt = {
                "procedural": MemoryType.PROCEDURAL,
                "factual": MemoryType.FACTUAL,
                "interpretive": MemoryType.INTERPRETIVE,
            }.get(ntype, MemoryType.FACTUAL)

            uid = f"ICC_{str(uuid.uuid4())[:6].upper()}"
            meta = {
                "source_question": question[:120],
                "inner_cc": True,
            }
            if trigger:
                meta["trigger"] = trigger

            mem = Memory(
                id=uid,
                narrative=narrative,
                memory_type=mt,
                source="inner_cc",
                confidence=confidence,
                context_of_encoding=f"inner_cc|{ntype}|code_architecture",
                metadata=meta,
            )
            cortex.store(mem)

            if parent_cp and parent_cp.startswith("CP"):
                try:
                    cortex.add_child(parent_cp, uid)
                except Exception as _bare_e:
                    logging.getLogger(__name__).warning(
                        "bare except in wild_igor/igor/tools/inner_cc.py: %s", _bare_e
                    )

            deposited += 1
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/tools/inner_cc.py: %s", _bare_e
            )
    return deposited


def _log_error(kind: str, detail: str) -> None:
    """Forensic log — lazy import to avoid circular dependency at module load."""
    try:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind=kind, detail=detail)
    except Exception:
        logging.getLogger(__name__).error("inner_cc %s: %s", kind, detail)


# ── Igor tool registration ────────────────────────────────────────────────────


def _tool_inner_cc(
    question: str,
    context: str = "",
    mode: str = "architecture",
    long_running: bool = False,
    **_,
) -> str:
    """Tool wrapper: calls inner_cc and returns formatted answer."""
    cortex = None
    try:
        from ..main import _running_instance

        if _running_instance is not None:
            cortex = _running_instance.cortex
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/tools/inner_cc.py: %s", _bare_e
        )

    model = _HAIKU_MODEL if long_running else _DEFAULT_MODEL
    result = call_inner_cc(
        question=question,
        context=context,
        mode=mode,
        cortex=cortex,
        model=model,
        long_running=long_running,
    )
    answer = result.get("answer", "(no answer)")
    nodes = result.get("nodes", [])
    follow = result.get("follow_up", "")

    lines = [answer]
    if nodes:
        lines.append(f"\n[Deposited {len(nodes)} node(s) to graph]")
    if follow:
        lines.append(f"\nFollow-up: {follow}")
    return "\n".join(lines)


registry.register(
    Tool(
        name="inner_cc",
        description=(
            "Igor's internal code-reasoning tool. Call this for programming, architecture, "
            "bash/batch/PowerShell, or codebase structure questions. Produces a direct answer "
            "AND deposits reusable graph nodes so the knowledge stays local next time. "
            "Use mode='architecture' for design questions, 'code_review' for reading code, "
            "'pattern' for extracting a general pattern, 'debug' for broken things, "
            "'curriculum' for structured training. "
            "Set long_running=True for multi-turn training passes (uses Haiku with prompt caching)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The code or architecture question to reason about.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional: code snippet, file path, or relevant detail to include.",
                },
                "mode": {
                    "type": "string",
                    "enum": [
                        "architecture",
                        "code_review",
                        "pattern",
                        "debug",
                        "curriculum",
                    ],
                    "description": "Reasoning mode. Default: architecture.",
                },
                "long_running": {
                    "type": "boolean",
                    "description": (
                        "If true, run a multi-turn session with Haiku + prompt caching. "
                        "Use for training passes, curriculum runs, deep analysis tasks."
                    ),
                },
            },
            "required": ["question"],
        },
        fn=_tool_inner_cc,
    )
)

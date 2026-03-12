"""
inner_cc — Igor's internal code-reasoning tool.

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

Modes:
  "architecture" — design decisions, tradeoffs, how-must-this-work
  "code_review"  — read a snippet and find issues / improvements
  "pattern"      — extract a general pattern from a specific example
  "debug"        — reason about why something is broken
  "curriculum"   — step through a training topic and deposit nodes (batch mode)
"""

import json
import os
import urllib.request
import uuid
from typing import Optional

from .registry import Tool, registry

OPENROUTER_BASE    = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/akienm/TheIgors"

# Best cheap model with strong code reasoning
_DEFAULT_MODEL = os.getenv("INNER_CC_MODEL", "openai/gpt-4o-mini")

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
    ("architecture", "What is the inertia principle in TheIgors and why does it exist?"),
    ("architecture", "How does the tier ladder decide which model handles a turn?"),
    ("architecture", "What is the difference between tree traversal and executive search in Igor's cognition?"),
    ("architecture", "Why is cloud inference reserved only for human interface turns?"),
    ("architecture", "What does 'LLMs are graph trainers' mean architecturally?"),
    ("architecture", "How do PROCEDURAL, FACTUAL, and INTERPRETIVE nodes differ in purpose?"),
    ("architecture", "What is the source/runtime split and why is it critical not to confuse them?"),
    ("architecture", "How does the word graph unify parsing and generation on the same weights?"),

    # ── Python patterns used in this codebase ────────────────────────────
    ("pattern", "What is the pattern: register a tool with a Tool() object and registry.register()?"),
    ("pattern", "What is the daemon-thread fire-and-forget pattern used in G53 habit extraction?"),
    ("pattern", "What is the SQLite WAL mode pattern and when should it be used?"),
    ("pattern", "What is the pattern for graceful degradation: try cloud, fallback to local, fallback to apology?"),
    ("pattern", "What is the SHA-256 cache-key pattern used in system_prompt.py and when is it appropriate?"),
    ("pattern", "What is the dataclass + SQLite serialization pattern used in memory/models.py?"),
    ("code_review", "What makes a good seed_*.py script in the claudecode/ directory?"),

    # ── Shell and cross-platform scripting ───────────────────────────────
    ("pattern", "What are the key differences between bash, DOS batch, and PowerShell for variable assignment and conditionals?"),
    ("pattern", "In bash, what is the pattern for safely sourcing a .env file without exporting credentials to child processes?"),
    ("pattern", "What is the nohup + background process pattern in bash, and when does it fail?"),
    ("pattern", "In PowerShell, what is the equivalent of bash's set -e (exit on error)?"),
    ("pattern", "What is the DOS batch pattern for checking if a file exists and branching?"),

    # ── Architecture heuristics (Akien's navigational fingerprint) ───────
    ("architecture", "Apply 'How must this work?' to the question: how must Igor route a turn when OpenRouter is out of budget?"),
    ("architecture", "Apply 'Where is the lever?' to reducing Igor's cloud inference costs."),
    ("architecture", "Apply 'How will us monkeys screw that up?' to Igor's self-edit capability."),
    ("architecture", "Apply 'What looks like it would fit there?' to the gap between word-graph and concept-tree layers."),

    # ── Reasoning about Igor's own code ──────────────────────────────────
    ("architecture", "When should Igor delegate codebase reasoning to Claude Code (cc.sh) instead of using an OpenRouter cloud turn?"),
    ("architecture", "What is the inertia principle and how does it determine whether Igor should self-edit a file or escalate to Claude Code?"),
    ("pattern", "What is the pattern for Igor to self-edit source files: read_source_file → patch_source_file → run_syntax_check → auto-commit?"),
    ("pattern", "What is the pattern for Igor to spawn Claude Code for an implementation task: write the brief to workspace/, then call cc.sh?"),
    ("architecture", "Why does Claude Code have a cost advantage over OpenRouter when reasoning about Igor's codebase, and when does that matter?"),
    ("architecture", "How should Igor decide whether a code change is within his own edit capability vs. needs Claude Code as the implementation worker?"),
    ("code_review", "When Igor reads his own source to understand a bug, what is the right sequence of tool calls to stay within budget?"),
    ("architecture", "What architectural patterns in TheIgors make the system self-describing — i.e., what files should Igor read first when starting a codebase reasoning task?"),
]


# ── Core call function ────────────────────────────────────────────────────────

def call_inner_cc(
    question: str,
    context: str = "",
    mode: str = "architecture",
    cortex=None,
    model: str = _DEFAULT_MODEL,
) -> dict:
    """
    Make one inner_cc call. Returns parsed JSON dict or error dict.
    Deposits nodes to cortex if provided.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"answer": "OPENROUTER_API_KEY not set", "nodes": [], "follow_up": ""}

    user_content = f"MODE: {mode}\n\nQUESTION: {question}"
    if context:
        user_content += f"\n\nCONTEXT:\n{context[:1200]}"

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": 400,
    }).encode()

    req = urllib.request.Request(
        f"{OPENROUTER_BASE}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": OPENROUTER_REFERER,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        raw = data["choices"][0]["message"]["content"].strip()
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {"answer": raw if "raw" in dir() else "parse error", "nodes": [], "follow_up": ""}
    except Exception as e:
        return {"answer": f"inner_cc error: {e}", "nodes": [], "follow_up": ""}

    # ── Deposit nodes to cortex ───────────────────────────────────────────────
    if cortex is not None:
        _deposit_nodes(result.get("nodes", []), cortex, question)

    return result


def _deposit_nodes(nodes: list, cortex, question: str) -> int:
    """Deposit extracted nodes to cortex. Returns count deposited."""
    from ..memory.models import Memory, MemoryType
    deposited = 0
    for node in nodes:
        try:
            ntype    = node.get("type", "factual").strip().lower()
            narrative = node.get("narrative", "").strip()
            confidence = float(node.get("confidence", 0.6))
            parent_cp  = node.get("parent_cp", "").strip()
            trigger    = node.get("trigger", "").strip()

            if not narrative or confidence < 0.55:
                continue

            mt = {
                "procedural":  MemoryType.PROCEDURAL,
                "factual":     MemoryType.FACTUAL,
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
                except Exception:
                    pass

            deposited += 1
        except Exception:
            pass
    return deposited


# ── Igor tool registration ────────────────────────────────────────────────────

def _tool_inner_cc(
    question: str,
    context: str = "",
    mode: str = "architecture",
    **_,
) -> str:
    """Tool wrapper: calls inner_cc and returns formatted answer."""
    import sys
    # Get cortex from Igor's running instance if available
    cortex = None
    try:
        from ..main import _running_instance
        if _running_instance is not None:
            cortex = _running_instance.cortex
    except Exception:
        pass

    result = call_inner_cc(question=question, context=context, mode=mode, cortex=cortex)
    answer = result.get("answer", "(no answer)")
    nodes  = result.get("nodes", [])
    follow = result.get("follow_up", "")

    lines = [answer]
    if nodes:
        lines.append(f"\n[Deposited {len(nodes)} node(s) to graph]")
    if follow:
        lines.append(f"\nFollow-up: {follow}")
    return "\n".join(lines)


registry.register(Tool(
    name="inner_cc",
    description=(
        "Igor's internal code-reasoning tool. Call this for programming, architecture, "
        "bash/batch/PowerShell, or codebase structure questions. Produces a direct answer "
        "AND deposits reusable graph nodes so the knowledge stays local next time. "
        "Use mode='architecture' for design questions, 'code_review' for reading code, "
        "'pattern' for extracting a general pattern, 'debug' for broken things, "
        "'curriculum' for structured training."
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
                "enum": ["architecture", "code_review", "pattern", "debug", "curriculum"],
                "description": "Reasoning mode. Default: architecture.",
            },
        },
        "required": ["question"],
    },
    fn=_tool_inner_cc,
))

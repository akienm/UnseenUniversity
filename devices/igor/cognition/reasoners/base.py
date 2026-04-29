"""base.py — Reasoner hierarchy, context assembly, and shared token economy.

WHAT IT IS
──────────
Reasoners are inference adapters: they translate Igor's internal state into
API-native protocol, execute conversation loops, handle tool calls, and
return plain text. Igor doesn't care which reasoner is active — it calls
reason() and gets text back.

This file defines:
  1. Two-level hierarchy (D026): transport base classes (stable structural
     distinctions) + model family failover.
  2. Shared token economy: context capping, tool result truncation,
     message trimming, cost tracking.
  3. Context assembly: _build_session_context() and _build_memory_context()
     wire ring memory, task sets, and thread anchors into the LLM window.
  4. Utilities for winnowing, context filtering, and local Ollama calls.

WHY IT EXISTS
─────────────
The inference gateway (inference_gateway.py) delegates to reasoners
instead of wiring directly to APIs. Separation buys:
  - Model names and API details hidden behind reason().
  - Cost discipline (token caps, budget checks, research-mode gates) in
    one place.
  - Safe failover (ModelFamily tries channels in order).
  - Context assembly anchored in one place (base.py); habits handle
    response dispatch. Splitting would make the boundary leaky.
  - Local-first (D211) — all reasoning flows through the gateway DAG,
    which selects tier by complexity + budget + cloud availability.

HOW IT WORKS (architecture)
───────────────────────────

HIERARCHY (D026 — two-level structure):

  Level 1 — Transport base classes (how inference talks to the outside):
    BaseReasoner(ABC, IgorBase)
    ├── LocalReasoner       — no cost, latency variance, no tools
    ├── APIReasoner         — budget tracking, rate limits, tool support
    └── BrowserReasoner     — declared placeholder (NOT IMPLEMENTED)

  Level 2 — Model family failover:
    ModelFamily(BaseReasoner)  — manages multiple channels; tries each
                                  in order, catches exceptions, escalates.
    └── ClaudeFamily(ModelFamily)

  Concrete instances:
    OllamaReasoner(LocalReasoner)   — tier.2 local inference
    OpenRouterReasoner(APIReasoner) — tier.3/3.5/4 cloud models via OR
    AnthropicReasoner               — historically here; removed per D188
                                      (igor-browser-or-only). Igor never
                                      calls Anthropic direct. Do not
                                      resurrect.

CONTRACT for reason() implementations:
  Input:
    user_input: str                   — the turn's text
    relevant_memories: list[Memory]   — cortex.search() results
    core_patterns: list[Memory]       — graph attractors / habits
    instance_id: str                  — Igor's instance name
    preparse_csb: str = ""            — [PARSED_INPUT] block (cloud only)
  Output:
    tuple[str, float]                 — (response_text, cost_in_usd)
  Failure mode:
    - Raises Exception on failure. Caller (gateway) catches and tries the
      next tier.
    - Idempotent: safe to retry same inputs.
    - Timeouts enforced by caller via threading.Event (exit_requested).

TOKEN ECONOMY (shared across all reasoners)
───────────────────────────────────────────
  TOOL_RESULT_MAX_CHARS = 8_000
    Every tool result capped before entering message history. Prevents
    one large read from blowing the window. Truncation notice visible.
  CONTEXT_WARN_CHARS = 80_000  (~20K tokens)
    Log warning; prompt human to step-break.
  CONTEXT_HARD_CAP_CHARS = 120_000
    Hard trim: drop oldest tool results; keep initial context + last 4
    turns. Inserts visible placeholder so model knows.
  MAX_TURNS = int(IGOR_MAX_TURNS env, default 8)
    Agentic loop safety limit. 0 = unlimited (reading sessions only).
  RESEARCH_MODE + BIG_READ_TOOLS / BASH_READ_PATTERNS
    When IGOR_RESEARCH_MODE=false, external API reads (confluence,
    web_search) are capped. Local file reads remain free and uncapped.
  Per-call cost cap removed (D206): budget floor + MAX_TURNS are
    sufficient.

CONTEXT ASSEMBLY (_build_session_context)
─────────────────────────────────────────
Injected string block; ordering matters:
  1. Thread anchors (T-thread-to-fallthrough) — most recent first; survive
     context trim; preserve conversation continuity.
  2. Task sets (category=task_set, limit=3) — active goals anchor all
     reasoning before urgency signals.
  3. High-urgency TWM (urgency ≥ 0.7, limit=5) — flagged distinctly so
     model notices (D028, Change 4).
  4. Ring memory anchor — NE summary from last ≤ 10 min; shows thread arc.
     Delta: recent ring entries since anchor (limit=5).
  5. Recent ring memory (fallback if no NE anchor) — last ≤ 10 entries,
     excluding {tool_trace, judgment, action_impulse, ne_diagnostic}.
Ring entries capped at RING_CONTEXT_LIMIT=10 total; entries older than
RING_CONTEXT_MAX_AGE_HOURS=8 are excluded from live context (still in DB
for cortex.search).

CONTEXT ASSEMBLY (_build_memory_context)
────────────────────────────────────────
High-relevance memories (relevance_score ≥ 0.5) formatted with temporal
anchors (stored today/yesterday/Nd ago/date), memory type, and narrative
snippet. Prevents old memories being treated as current reality.

WINNOWING (pre-filter for expensive calls)
──────────────────────────────────────────
_winnow_context_method() pre-filters before cloud calls:
  1. Reads ring breadcrumbs (last 5 entries, oldest first)
  2. Activates word_graph concepts from user input
  3. Prompts cheap model: "List 2-3 specific memory searches"
  4. Fetches Memory objects for each query (limit=2 per query)
  5. Deposits INTERPRETIVE node: "When context involves [keywords],
     search for [queries]" — trains the graph to route context without
     model calls over time.
  6. Returns augmented relevant_memories list.
Skipped if: input < 20 chars, starts with /, IGOR_CONTEXT_WINNOW=false,
no cortex. Note: _winnow_context_method is bound at module bottom as a
BaseReasoner method (indentation pattern; tidy on future touch).

prompt_role threading (optional per-call override)
──────────────────────────────────────────────────
  - prompt_role (None by default) gates persona in cloud reasoners.
  - None → default role per tier (interactive vs analysis).
  - Not in BaseReasoner.reason() signature; reasoners accept it as
    **kwargs.
  - OpenRouterReasoner + system_prompt.py handle the override.
  - OllamaReasoner ignores prompt_role; no persona switching locally.

RESPONSE SHAPE
──────────────
Plain str (all reasoners). Cost in USD: OllamaReasoner returns 0.0;
OpenRouter returns per-token cost. No structured JSON wrapping at this
layer. NE uses response_format:json_object (D053) and parses the JSON —
that's NE's concern, not base.reason()'s.

RELATIONSHIP TO inference_gateway.py
────────────────────────────────────
Reasoners are the LEAVES of the gateway DAG. Gateway:
  1. Builds reasoner (from_env() at boot, cached)
  2. Assembles InferenceContext (cloud_active, local_available, …)
  3. Selects tier by complexity + budget + cloud availability
  4. Calls tier.reason() with assembled messages
  5. Catches exceptions and tries next tier (ModelFamily failover)
Reasoners DON'T know about tiers, cloud budget, availability gates, or
DAG routing. That separation makes inference_gateway.py the policy layer;
reasoners are execution units.

KEY DECISIONS SHAPING THIS SUBSYSTEM
────────────────────────────────────
  D015  gateway-pattern          — DAG routing in gateway; policy in one
                                    file
  D026  reasoner-hierarchy       — two-level: transport + model family
  D028  urgency signals          — high-urgency TWM flagged distinctly
  D035  interactive-persona-tier — Haiku (tier.3.5) between cheap + Sonnet
  D053  NE JSON response         — response_format:json_object
  D071  cloud-ok runtime switch  — file-backed TTL for night/local-only
  D188  igor-browser-or-only     — AnthropicReasoner removed; never call
                                    Anthropic direct
  D206  remove per-call cost cap — budget floor + MAX_TURNS sufficient
  D211  local-first inference    — tier.2 primary; cloud only for high/med
  D234  tier-ladder redesign     — Ollama primary, OR luxury
  D259  human-author routing     — is_user_turn gates background escalation
  D327  inference encapsulation  — ollama_reasoner + openrouter_reasoner
                                    consolidate earlier reasoner files

Eventually: less cloud inference, more habit execution — pure habits
replace some reasoning. That's the North Star; this file is the
current-state interface.

If you want to change:
  - Token caps / context limits — edit TOOL_RESULT_MAX_CHARS, MAX_TURNS,
                                   CONTEXT_HARD_CAP_CHARS at top.
  - Context assembly order      — edit _build_session_context() logic.
  - Winnowing behavior          — edit _winnow_context_method() +
                                   _deposit_winnow_node().
  - Reasoner contract           — edit BaseReasoner.reason() signature
                                   (HIGH inertia — discuss first).

Updated 2026-04-29T17:08:53Z
"""

import logging

import json
import os
import threading
import urllib.request
from abc import ABC, abstractmethod
from ...memory.models import Memory, MemoryType
from ...igor_base import IgorBase

# ── Global exit signal ─────────────────────────────────────────────────────────
# Set by main._stdin_reader when /exit or /quit is typed.
# Checked at the top of each agentic turn so a long API call stops at the
# next turn boundary instead of running to completion.
exit_requested: threading.Event = threading.Event()

# Shared ring-context constants (WO8: single source of truth — was duplicated
# verbatim in anthropic.py and openrouter_reasoner.py)
_RING_EXCLUDE = frozenset({"tool_trace", "judgment", "action_impulse", "ne_diagnostic"})
_RING_CONTEXT_LIMIT = 10
# #199: Exclude ring entries older than this from LLM context injection.
# Entries stay in DB for cortex.search() / history — only filtered from live context.
# Default 8h keeps same-day context, drops yesterday's stale actions/completions.
import os as _os

_RING_CONTEXT_MAX_AGE_HOURS = float(_os.getenv("IGOR_RING_CONTEXT_MAX_AGE_HOURS", "8"))

# ── Token economy (shared across all reasoners) ────────────────────────────────
# Each tool result is capped before it enters the message history.
# This prevents a single large command output (find, cat big file, etc.) from
# blowing up the context window.  Big tasks should be decomposed, not ingested
# in one shot.
TOOL_RESULT_MAX_CHARS = (
    8_000  # ~2 K tokens — enough for real data; trim forces decomposition
)
MAX_TURNS = int(
    os.getenv("IGOR_MAX_TURNS", "8")
)  # env-overridable; 0 = unlimited; default 8 prevents runaway agentic burns
CONTEXT_WARN_CHARS = 80_000  # ~20 K tokens — warn earlier, prompt breaking into steps
CONTEXT_HARD_CAP_CHARS = 120_000  # hard trim — drop oldest tool results above this

# ── Cost guardrails (shared across all API reasoners) ─────────────────────────
# IGOR_RESEARCH_MODE: set true to allow bulk reads (confluence, source files, web).
# IGOR_RESEARCH_TOOL_CAP: max big-read tool calls per reasoning session when not in research mode.
RESEARCH_TOOL_CAP = int(os.getenv("IGOR_RESEARCH_TOOL_CAP", "5"))
RESEARCH_MODE = os.getenv("IGOR_RESEARCH_MODE", "false").lower() in ("1", "true", "yes")

# Tools that constitute expensive external reads — capped when not in research mode.
# Local file reads (read_source_file, list_source_files) are free and NOT capped.
# Only external API calls that cost money or tokens are gated.
BIG_READ_TOOLS = frozenset(
    {
        "confluence_search",
        "confluence_get_page",
        "web_search",
    }
)

# Bash command prefixes that indicate external/expensive operations via run_bash.
# Plain file reads via bash are NOT counted — only network/search patterns.
BASH_READ_PATTERNS = ("curl ", "wget ")


class BaseReasoner(ABC, IgorBase):
    """
    A reasoning adapter translates Igor's internal state into whatever
    protocol a specific AI speaks, executes the conversation, handles
    tool calls, and returns a plain text response.

    Igor doesn't care which reasoner is active. It calls reason() and
    gets text back.
    """

    @abstractmethod
    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
        preparse_csb: str = "",
    ) -> tuple[str, float]:
        """
        Generate a response.
        Returns (response_text, cost_in_usd).
        preparse_csb: structured PARSED_INPUT block injected into context for cloud reasoners.
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this reasoner."""
        ...

    # ── Token economy ─────────────────────────────────────────────────────────

    @staticmethod
    def _cap_tool_result(result: str) -> str:
        """
        Truncate a tool result to TOOL_RESULT_MAX_CHARS.

        Appends a visible truncation notice so the model knows output was cut.
        Big outputs (find trees, large files) must be broken into smaller steps,
        not ingested whole — this cap enforces that discipline.
        """
        if len(result) <= TOOL_RESULT_MAX_CHARS:
            return result
        dropped = len(result) - TOOL_RESULT_MAX_CHARS
        return (
            result[:TOOL_RESULT_MAX_CHARS]
            + f"\n[TRUNCATED — {dropped} more chars not shown. "
            f"Break large tasks into smaller steps rather than reading everything at once.]"
        )

    @staticmethod
    def _messages_total_chars(messages: list) -> int:
        """Rough char count of all message content — used for context size warnings."""
        total = 0
        for m in messages:
            c = m.get("content") or ""
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                for block in c:
                    if isinstance(block, dict):
                        total += len(
                            str(block.get("text", "") or block.get("content", ""))
                        )
                    else:
                        total += len(str(block))
        return total

    @staticmethod
    def _trim_messages(messages: list) -> list:
        """
        Hard context cap (#26): if total message chars exceed CONTEXT_HARD_CAP_CHARS,
        drop middle messages (oldest tool results) while preserving:
          - messages[0]: the initial user message with all injected context
          - messages[-4:]: the 4 most recent messages (current tool round)

        Inserts a visible placeholder so the model knows history was trimmed.
        Returns the trimmed list (or the original if under cap).
        """
        if len(messages) <= 3:
            return messages

        total = BaseReasoner._messages_total_chars(messages)
        if total <= CONTEXT_HARD_CAP_CHARS:
            return messages

        # Keep first (initial context) + last 4 (most recent reasoning)
        keep_tail = min(4, len(messages) - 1)
        dropped = messages[1 : len(messages) - keep_tail]
        dropped_chars = BaseReasoner._messages_total_chars(dropped)
        trimmed = (
            [messages[0]]
            + [
                {
                    "role": "user",
                    "content": f"[CONTEXT TRIMMED: {len(dropped)} older messages ({dropped_chars // 1000}K chars) "
                    f"dropped to stay within context limit. Ask me to recap if needed.]",
                }
            ]
            + messages[len(messages) - keep_tail :]
        )
        return trimmed

    # ── Shared tool-call display (#34) ────────────────────────────────────────

    @staticmethod
    def print_tool_call(
        tag: str, turn: int, name: str, args_summary: str, result_preview: str
    ):
        """
        Uniform tool-call display across all reasoners.
        tag: short reasoner label, e.g. "THINK" or "OR"
        """
        from rich.console import Console as _Console

        _c = _Console(force_terminal=True)
        _c.print(f"[dim][{tag} turn={turn}] ⚙ {name}({args_summary})[/]")
        _c.print(f"[dim][{tag} turn={turn}]   → {result_preview}[/]")

    # ── Shared context builders (WO8) ─────────────────────────────────────────

    def _build_session_context(self, cortex, thread_id: str | None = None) -> str:
        """
        Recent ring memory as session context block. Empty string if nothing relevant.

        Change 4 (D028): high-urgency TWM obs (urgency ≥ 0.7) are flagged distinctly
        at the top of the context injection so the model notices them first.

        #158: TASK_SET entries for this thread go first — before ring, before all else.
        They represent the active goal of this attention nexus and must outcompete
        ambient ring content (the SSH/Hamlet problem).
        """
        if cortex is None:
            return ""
        all_entries = cortex.read_ring_memory(limit=50, thread_id=thread_id)
        # #199: drop entries older than _RING_CONTEXT_MAX_AGE_HOURS from live context.
        # They remain in DB for history/search — just not injected into LLM context.
        try:
            from datetime import datetime as _dt2

            _cutoff = _dt2.now().timestamp() - _RING_CONTEXT_MAX_AGE_HOURS * 3600
            all_entries = [
                e
                for e in all_entries
                if _dt2.fromisoformat(e["timestamp"]).timestamp() >= _cutoff
            ]
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/reasoners/base.py: %s", _bare_e
            )
        filtered = [e for e in all_entries if e["category"] not in _RING_EXCLUDE]
        entries = filtered[-_RING_CONTEXT_LIMIT:]

        lines = []

        # ── T-thread-to-fallthrough: thread anchor — orientation before all else ──
        # Reads the most recent turn-anchors from DB (EPISODIC, thread_anchor=true).
        # These survive context window trimming because they are written to persistent
        # storage at each turn boundary, not assembled from the live context window.
        # Prepended first so a post-trim Igor knows what conversation it is in
        # before reading any task goal, urgency signal, or ring entry.
        try:
            from ...tools.thread_anchor import read_thread_anchor as _read_anchor

            _anchor_block = _read_anchor(cortex, limit=3)
            if _anchor_block:
                lines.append(_anchor_block)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "thread anchor read failed: %s", _bare_e
            )

        # ── #158: TASK_SET first — active goal anchors all context ────────────
        try:
            task_sets = cortex.twm_read(
                limit=3,
                include_integrated=False,
                thread_id=thread_id,
                category="task_set",
            )
            if task_sets:
                lines.append("🎯 ACTIVE TASK (complete this before anything else):")
                for t in task_sets:
                    goal = t["content_csb"].replace("TASK_SET|", "").strip()
                    lines.append(f"  → {goal[:200]}")
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/reasoners/base.py: %s", _bare_e
            )

        # ── Change 4: inject high-urgency TWM observations ────────────────────
        try:
            twm_obs = cortex.twm_read(
                limit=15, include_integrated=False, thread_id=thread_id
            )
            urgent = [
                o
                for o in twm_obs
                if o.get("urgency", 0.2) >= 0.7
                and o.get("source") not in ("narrative_engine", "ne_loop_guard")
                and o.get("category") != "task_set"  # already shown above
            ]
            if urgent:
                lines.append("\n⚠ URGENT observations (act on these):")
                for o in sorted(
                    urgent,
                    key=lambda x: x.get("urgency", 0.2) * x.get("salience", 0.5),
                    reverse=True,
                )[:5]:
                    urg = o.get("urgency", 0.2)
                    lines.append(f"  [urgency={urg:.1f}] {o['content_csb'][:150]}")
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/reasoners/base.py: %s", _bare_e
            )

        if not entries:
            return "\n".join(lines) if lines else ""

        # ── Temporal anchor: use NE narrative summary if fresh (≤ 10 min) ─────
        # Find most recent 'narrative' ring entry for this thread (or global).
        # If found and fresh, use it as [Thread arc: ...] and only show delta
        # ring entries AFTER it (cap 5). Falls back to full last-10 if no anchor.
        anchor_content = None
        anchor_ts = None
        try:
            from datetime import datetime as _dt

            _narrative_entries = cortex.read_ring_memory(
                limit=5, category="narrative", thread_id=thread_id
            )
            if _narrative_entries:
                _latest_ne = _narrative_entries[-1]  # newest last
                _ne_ts_str = _latest_ne["timestamp"]
                _ne_dt = _dt.fromisoformat(_ne_ts_str)
                _age_s = (_dt.now() - _ne_dt).total_seconds()
                if _age_s <= 600:  # 10 min
                    anchor_content = _latest_ne["content"]
                    anchor_ts = _ne_ts_str
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/reasoners/base.py: %s", _bare_e
            )

        # Helper: format a ring-entry timestamp so Igor can distinguish
        # "this happened 3 days ago" from "this is happening right now."
        # Same-day entries show HH:MM; older entries show YYYY-MM-DD HH:MM.
        from datetime import date as _rdate

        _ring_today = _rdate.today().isoformat()

        def _ring_ts(raw_ts: str) -> str:
            if len(raw_ts) < 10:
                return raw_ts
            if raw_ts[:10] == _ring_today:
                return raw_ts[11:16] if len(raw_ts) >= 16 else raw_ts
            return raw_ts[:16]  # YYYY-MM-DD HH:MM — date visible when not today

        if anchor_content:
            # Strip NE run tag for readability: "[NE#42] text" → "text"
            _arc = anchor_content
            if _arc.startswith("[NE#"):
                _arc = _arc[_arc.find("] ") + 2 :] if "] " in _arc else _arc
            lines.append(f"\n[Thread arc: {_arc[:240]}]")
            # Delta: ring entries AFTER the anchor timestamp, excluding narrative category
            delta = [
                e
                for e in entries
                if e["timestamp"] > anchor_ts
                and e["category"] not in _RING_EXCLUDE
                and e["category"] != "narrative"
            ][-5:]
            if delta:
                lines.append("Recent context (since last arc):")
                for e in delta:
                    lines.append(f"[{_ring_ts(e['timestamp'])}] {e['content']}")
        else:
            lines.append("\n\nRecent session context (newest last):")
            for e in entries:
                lines.append(f"[{_ring_ts(e['timestamp'])}] {e['content']}")
        return "\n".join(lines)

    def _build_memory_context(self, memories: list[Memory]) -> str:
        """Top relevant memories as a context block. Empty string if none qualify."""
        if not memories:
            return ""
        high_rel = [m for m in memories if getattr(m, "relevance_score", 0.0) >= 0.5][
            :3
        ]
        if not high_rel:
            high_rel = sorted(
                memories[:5],
                key=lambda m: getattr(m, "relevance_score", 0.0),
                reverse=True,
            )[:2]
        if not high_rel:
            return ""
        from datetime import datetime as _mdt, date as _mdate

        _today = _mdate.today()
        lines = ["\n\nRelevant memories:"]
        for m in high_rel:
            # Temporal anchor: Igor can see exactly when a memory was stored.
            # This prevents treating old memories as current reality.
            try:
                ts = m.timestamp
                if isinstance(ts, str):
                    ts = _mdt.fromisoformat(ts.replace("Z", "+00:00"))
                if hasattr(ts, "date"):
                    d = ts.date()
                    age_days = (_today - d).days
                    if age_days == 0:
                        _ts_label = f"today {ts.strftime('%H:%M')}"
                    elif age_days == 1:
                        _ts_label = f"yesterday {ts.strftime('%H:%M')}"
                    elif age_days < 30:
                        _ts_label = f"{age_days}d ago ({d})"
                    else:
                        _ts_label = str(d)
                else:
                    _ts_label = "?"
            except Exception:
                _ts_label = "?"
            lines.append(
                f"- [{m.memory_type.value} | stored {_ts_label}] {m.narrative}"
            )
        return "\n".join(lines)


def _call_ollama_raw(prompt: str, model: str, timeout: int = 5) -> str | None:
    """
    Call local Ollama /api/chat. Returns response text or None on failure.
    OLLAMA_HOST env var overrides endpoint (default http://localhost:11434).
    Dual-homed model pattern: same model family runs locally and on OR;
    local is faster/cheaper, OR is the fallback. (#188)
    """
    try:
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.1},
            }
        ).encode()
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data.get("message", {}).get("content", "").strip() or None
    except Exception:
        return None


def _deposit_winnow_node(user_input: str, queries: list[str], cortex) -> None:
    """
    Deposit an INTERPRETIVE node after a successful winnow: captures
    "when context involves [keywords], search for [queries]."
    Trains the graph to route context without a model call over time. (#188)

    T-winnow-trigger-fix: strip think_context prefix so keywords come from
    actual user input, not internal format. Without this, triggers contain
    [think_context] and never match real messages (267/267 = 100% dead weight).
    """
    try:
        import hashlib

        # T-winnow-trigger-fix: extract actual user message from think_context wrapper
        clean_input = user_input
        if "[think_context]" in clean_input.lower() or "[THINK_CONTEXT]" in clean_input:
            # Take only content after the last newline with actual user text
            lines = clean_input.split("\n")
            # Find lines that look like user messages (not think_context metadata)
            user_lines = [
                ln
                for ln in lines
                if ln.strip()
                and not ln.strip().startswith("[")
                and not ln.strip().startswith("intent=")
                and "think_context" not in ln.lower()
                and "complexity=" not in ln
                and "affect:" not in ln
            ]
            clean_input = " ".join(user_lines[-3:]) if user_lines else user_input

        _STOP = {
            "the",
            "and",
            "for",
            "that",
            "this",
            "with",
            "have",
            "from",
            "what",
            "how",
            "can",
            "you",
            "are",
            "its",
            "but",
        }
        words = [w.lower().strip(".,?!") for w in clean_input.split() if len(w) > 3]
        keywords = [w for w in words if w not in _STOP][:4]
        if not keywords or not queries:
            return
        narrative = (
            f"When context involves [{', '.join(keywords)}], "
            f"search for [{'; '.join(queries)}]."
        )
        node_id = (
            "WINNOW_" + hashlib.sha256(narrative.encode()).hexdigest()[:10].upper()
        )
        mem = Memory(
            id=node_id,
            narrative=narrative,
            memory_type=MemoryType.INTERPRETIVE,
            activation_count=0,
            valence=0.5,
            metadata={
                "source": "winnow",
                "trigger": " ".join(keywords),
                "confidence": 0.6,
            },
        )
        cortex.store(mem)
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/cognition/reasoners/base.py: %s", _bare_e
        )


def _winnow_context_method(
    self, user_input: str, cortex, word_graph=None
) -> list[Memory]:
    """
    Pre-call context filter — the breadcrumb step.

    Before the main reasoning call, ask a cheap model:
    "Given what we've been talking about and this new input,
    what specific memories do you need?"

    Returns targeted Memory objects to merge into relevant_memories.
    Skipped if: input is short/command, IGOR_CONTEXT_WINNOW=false, no OR key.

    This is the winnowing loop: smaller calls more often, converging on
    the relevant context rather than dumping everything every time.
    """
    import time as _wtime

    _w_t0 = _wtime.monotonic()
    # Skip for trivial inputs
    if len(user_input.strip()) < 20 or user_input.strip().startswith("/"):
        return []
    if os.getenv("IGOR_CONTEXT_WINNOW", "true").lower() in ("false", "0", "no"):
        return []
    if cortex is None:
        return []

    # ── Build compact breadcrumb trail from ring ───────────────────────────
    try:
        ring = cortex.read_ring_memory(limit=10)
        filtered = [e for e in ring if e["category"] not in _RING_EXCLUDE]
        breadcrumbs = "\n".join(
            f"[{e['timestamp'][11:16]}] {e['content'][:80]}" for e in filtered[-5:]
        )
    except Exception:
        breadcrumbs = ""

    # ── Word graph hints: concepts activated by this input ─────────────────
    wg_hints = ""
    if word_graph is not None:
        try:
            predicted = word_graph.predict_next(user_input, n=5)
            if predicted:
                wg_hints = "Activated concepts: " + ", ".join(w for w, _ in predicted)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/reasoners/base.py: %s",
                _bare_e,
            )

    prompt = (
        f"Context trail:\n{breadcrumbs}\n\n"
        f"{wg_hints}\n\n"
        f"New input: {user_input[:200]}\n\n"
        "List 2-3 specific memory search queries (comma-separated, 2-4 words each) "
        "to retrieve the most relevant context for responding. Be specific. No explanation."
    )

    # ── Model call: inference gateway (local Ollama → OR fallback) ─────────
    queries: list[str] = []
    try:
        from ..inference_gateway import get_gateway as _gw, make_context as _mk_ctx

        _text = _gw().call("winnow", prompt, _mk_ctx(is_background=False))
        if _text:
            queries = [
                q.strip() for q in _text.replace("\n", ",").split(",") if q.strip()
            ][:3]
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/cognition/reasoners/base.py: %s", _bare_e
        )

    if not queries:
        return []

    # ── Fetch memories for each query, dedupe ─────────────────────────────
    results: list[Memory] = []
    seen_ids: set[str] = set()
    for q in queries:
        try:
            found = cortex.search(q, limit=2)
            for m in found:
                if m.id not in seen_ids:
                    seen_ids.add(m.id)
                    results.append(m)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/reasoners/base.py: %s",
                _bare_e,
            )

    # ── Deposit: train the graph on what we just routed (#188) ────────────
    if results:
        _deposit_winnow_node(user_input, queries, cortex)

    # ── Pipeline trace ─────────────────────────────────────────────────────
    try:
        from ...cognition.forensic_logger import (
            log_pipeline_step as _log_pt,
            get_turn_id as _get_turn_id,
        )

        _log_pt(
            turn_id=_get_turn_id(),
            step="winnow",
            elapsed_ms=round((_wtime.monotonic() - _w_t0) * 1000),
            queries=len(queries),
            retrieved=len(results),
        )
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/cognition/reasoners/base.py: %s", _bare_e
        )

    return results


# Bind _winnow_context_method as a method on BaseReasoner.
# It was accidentally nested inside _deposit_winnow_node (misindentation bug).
BaseReasoner._winnow_context = _winnow_context_method  # type: ignore[attr-defined]

# ── Level 1 — Transport base classes (Change 2 / D026) ────────────────────────


class LocalReasoner(BaseReasoner):
    """
    Base for all local-hardware reasoners (Ollama).
    No API cost. Latency varies with hardware. No tool support.
    """

    supports_tools: bool = False
    response_format: str = "unstructured"
    cost_model: str = "free"
    reliability: str = "medium"
    supports_context_param: bool = False


class APIReasoner(BaseReasoner):
    """
    Base for all cloud API reasoners (Anthropic, OpenRouter).
    Has budget tracking, rate limits, and reliable tool support.
    Subclasses are expected to call record_spend() and check_before_call().
    """

    supports_tools: bool = True
    response_format: str = "structured"
    cost_model: str = "per_token"
    reliability: str = "high"
    supports_context_param: bool = False


class BrowserReasoner(BaseReasoner):
    """
    Placeholder for future browser-session AI access.
    Zero cost. Session-fragile. No tools. NOT IMPLEMENTED.
    Declared here to reserve the interface and document the capability model.
    """

    supports_tools: bool = False
    response_format: str = "unstructured"
    cost_model: str = "free"
    reliability: str = "low"
    supports_context_param: bool = False

    def reason(
        self, user_input, relevant_memories, core_patterns, instance_id, cortex=None
    ):
        raise NotImplementedError("BrowserReasoner is not yet implemented.")

    def name(self) -> str:
        return "BrowserReasoner(not_implemented)"


# ── Level 2 — Model family classes (Change 2 / D026) ─────────────────────────


class ModelFamily(BaseReasoner):
    """
    Groups multiple channels by model identity. Handles failover across channels.
    Tries channels in order; moves to next on budget exhaustion or unavailability.
    Logs which channel was used and why fallback triggered.
    """

    channels: list[BaseReasoner] = []

    def reason(
        self,
        user_input: str,
        relevant_memories: list,
        core_patterns: list,
        instance_id: str,
        cortex=None,
    ) -> tuple[str, float]:
        last_exc = None
        for channel in self.channels:
            try:
                return channel.reason(
                    user_input,
                    relevant_memories,
                    core_patterns,
                    instance_id,
                    cortex=cortex,
                )
            except Exception as exc:
                last_exc = exc
                continue
        raise RuntimeError(f"{self.name()} all channels failed. Last: {last_exc}")

    def name(self) -> str:
        return f"ModelFamily({', '.join(c.name() for c in self.channels)})"


class ClaudeFamily(ModelFamily):
    """
    Claude model family across all available channels:
      1. AnthropicReasoner (direct API — fastest, most reliable)
      2. OpenRouterReasoner pointing at claude-sonnet (OR budget)
      3. BrowserReasoner (declared only; not yet implemented)

    Channels are populated at runtime from available credentials.
    Per-channel spend is tracked independently; combined Claude spend is the sum.
    """

    def name(self) -> str:
        if self.channels:
            return f"ClaudeFamily({', '.join(c.name() for c in self.channels)})"
        return "ClaudeFamily(no_channels)"

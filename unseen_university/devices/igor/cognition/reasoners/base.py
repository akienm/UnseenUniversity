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
# tags: Cognition, Architecture
"""

import json
import os
import threading
import urllib.request

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
_RING_CONTEXT_MAX_AGE_HOURS = float(os.getenv("IGOR_RING_CONTEXT_MAX_AGE_HOURS", "8"))

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



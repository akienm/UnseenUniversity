"""
tiered_research.py — T-tiered-research-tool (#450)

Research tool that tries cheapest sources first:
  1. Memory search (free, instant)
  2. Web search via DuckDuckGo (free, ~1s)
  3. Local Ollama synthesis (free, ~5s)
  4. Cloud LLM synthesis (paid, ~3s)

Stops at the first tier that produces a useful answer. Returns the
answer plus provenance (which tier resolved it + cost).

Inertia: LOW (new tool)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from ..paths import paths as _paths
from lab.utility_closet.registry import Tool, registry

logger = logging.getLogger(__name__)

_TIERS = ["memory", "web", "local_llm", "cloud_llm"]


def tiered_research(query: str, max_tier: str = "cloud_llm") -> str:
    """Research a question using progressively more expensive sources.

    max_tier: stop escalation at this tier (default: cloud_llm).
      Options: memory, web, local_llm, cloud_llm
    """
    if not query or not query.strip():
        return "[tiered_research] empty query"

    max_idx = _TIERS.index(max_tier) if max_tier in _TIERS else len(_TIERS) - 1

    for i, tier in enumerate(_TIERS):
        if i > max_idx:
            break
        try:
            result = _try_tier(tier, query)
            if result:
                return f"[{tier}] {result}"
        except Exception as exc:
            logger.debug("tiered_research tier %s failed: %s", tier, exc)

    return f"[tiered_research] no tier resolved: {query[:100]}"


def _try_tier(tier: str, query: str) -> Optional[str]:
    if tier == "memory":
        return _tier_memory(query)
    elif tier == "web":
        return _tier_web(query)
    elif tier == "local_llm":
        return _tier_local_llm(query)
    elif tier == "cloud_llm":
        return _tier_cloud_llm(query)
    return None


def _tier_memory(query: str) -> Optional[str]:
    """Search Igor's own memory for an answer."""
    try:
        from ..memory.cortex import Cortex

        cortex = Cortex(None)
        results = cortex.search(query, limit=3)
        if not results:
            return None
        top = results[0]
        if len(top.narrative) < 20:
            return None
        narratives = "\n".join(f"- {m.narrative[:200]}" for m in results[:3])
        return f"From memory:\n{narratives}"
    except Exception:
        return None


def _tier_web(query: str) -> Optional[str]:
    """Search the web via DuckDuckGo."""
    try:
        from .web_search import web_search

        result = web_search(query, max_results=3)
        if not result or "No results found" in result:
            return None
        return result[:1500]
    except Exception:
        return None


def _tier_local_llm(query: str) -> Optional[str]:
    """Ask local Ollama for synthesis."""
    try:
        from ..cognition.inference_gateway import get_gateway, make_context

        gw = get_gateway()
        text, cost, used_api = gw.call(
            "research",
            f"Answer this concisely: {query}",
            make_context(),
        )
        if text and not text.startswith("⚠"):
            return text[:1500]
        return None
    except Exception:
        return None


def _tier_cloud_llm(query: str) -> Optional[str]:
    """Ask cloud LLM for synthesis (costs money)."""
    if os.getenv("IGOR_CLOUD_BLOCKED", "").lower() in ("1", "true", "yes"):
        return None
    try:
        from ..cognition.inference_gateway import get_gateway, make_context

        gw = get_gateway()
        text, cost, used_api = gw.reason(
            f"Answer this concisely: {query}",
            relevant=[],
            core=[],
            level="interactive",
        )
        if text and not text.startswith("⚠"):
            return f"{text[:1500]} (cost: ${cost:.4f})"
        return None
    except Exception:
        return None


registry.register(
    Tool(
        name="tiered_research",
        description=(
            "Research a question using progressively expensive sources: "
            "memory → web → local LLM → cloud LLM. Stops at the first "
            "tier that resolves. Set max_tier to cap escalation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The research question",
                },
                "max_tier": {
                    "type": "string",
                    "description": "Stop at this tier: memory, web, local_llm, cloud_llm (default: cloud_llm)",
                },
            },
            "required": ["query"],
        },
        fn=tiered_research,
    )
)


# ── research_and_deposit — habit-dispatchable wrapper (T-tiered-research-habit) ─
#
# Habit auto-dispatch needs a one-required-arg tool (main.py:5218 passes
# core_input as the single required arg). `tiered_research` already qualifies,
# but the habit layer ALSO wants the answer deposited into cortex as a FACTUAL
# memory so future graph searches can find it without re-invoking the tool.
#
# Separating concerns: tiered_research stays read-only (its natural shape for
# direct tool calls). research_and_deposit wraps it + deposits. Habit binding
# goes to this wrapper.


_RESEARCH_QUERY_PREFIXES = (
    "research ",
    "look up ",
    "find out about ",
    "tell me about ",
    "what do you know about ",
    "what can you tell me about ",
)


def _extract_research_query(text: str) -> str:
    """Strip the triggering prefix from message text to leave the bare subject.

    'research X'        → 'X'
    'look up X'         → 'X'
    'tell me about X'   → 'X'
    anything else       → returned as-is (habit may still dispatch on full text)
    """
    low = text.strip().lower()
    for prefix in _RESEARCH_QUERY_PREFIXES:
        if low.startswith(prefix):
            return text.strip()[len(prefix) :].strip() or text.strip()
    return text.strip()


def research_and_deposit(query: str) -> str:
    """Run tiered_research and deposit the answer as a FACTUAL memory.

    Returns the answer text (same shape as tiered_research) so callers see
    the result immediately. The deposit is best-effort — if it fails, the
    research result still returns cleanly.
    """
    if not query or not query.strip():
        return "[research_and_deposit] empty query"

    subject = _extract_research_query(query)
    answer = tiered_research(subject)

    # Skip deposit when tiered_research returned a failure sentinel. Those
    # strings ("[tiered_research] no tier resolved: …", "[tiered_research]
    # empty query", any "[<tier>] " with empty content) would pollute the
    # graph with placeholder memories that future cosine/text search would
    # treat as real answers. Only deposit genuine content.
    _is_failure = (
        not answer
        or answer.startswith("[tiered_research] ")
        or answer.strip() in ("", "[memory] ", "[web] ", "[local_llm] ", "[cloud_llm] ")
    )
    if _is_failure:
        return answer

    # Best-effort deposit — never fail the primary return on deposit errors.
    try:
        from .graph_write import store_memory

        deposit_narrative = f"Q: {subject}\nA: {answer}"
        store_memory(
            narrative=deposit_narrative,
            memory_type="FACTUAL",
            tags="research,tiered_research",
            source="research_and_deposit",
            context=f"research habit triggered by: {query[:100]}",
        )
    except Exception as exc:
        logger.debug("research_and_deposit store failed: %s", exc)

    return answer


registry.register(
    Tool(
        name="research_and_deposit",
        description=(
            "Research a question with tiered_research (memory→web→local→cloud) AND "
            "deposit the Q/A pair as a FACTUAL memory so future graph searches can "
            "find it. Use when Akien asks to 'research X', 'look up X', 'tell me "
            "about X' — the habit layer auto-dispatches to this on those phrases."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The research question or topic (may include the triggering prefix — it will be stripped).",
                },
            },
            "required": ["query"],
        },
        fn=research_and_deposit,
    )
)

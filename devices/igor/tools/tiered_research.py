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
from .registry import Tool, registry

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

"""
inference_openrouter.py — D327: Unified OpenRouter cloud inference.

Consolidates openrouter_reasoner.py into one module.
This is the ONLY file that knows about OpenRouter mechanics.
D329: Also handles all cloud routing — no separate Anthropic direct path.

Phase 1: re-exports from original location (migration shim).
Phase 1b: actual code moves here, old file becomes thin redirect.

Public API (what the gateway calls):
  - OpenRouterReasoner class (interactive cloud reasoning)
"""

from __future__ import annotations

# ── From openrouter_reasoner ───────────────────────────────────────────────
import os as _os

from .reasoners.openrouter_reasoner import (
    OpenRouterReasoner,
    MODEL_ALIASES,
    OPENROUTER_BASE,
)

# D327: Cheap/interactive model constants — single source of truth.
# TODO(D327-cfg): move to cfg file.
OR_CHEAP_MODEL = _os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")
OR_INTERACTIVE_MODEL = _os.getenv(
    "OPENROUTER_INTERACTIVE_MODEL", "anthropic/claude-sonnet-4-6"
)

__all__ = [
    "OpenRouterReasoner",
    "MODEL_ALIASES",
    "OPENROUTER_BASE",
    "OR_CHEAP_MODEL",
    "OR_INTERACTIVE_MODEL",
]

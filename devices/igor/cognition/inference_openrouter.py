"""
inference_openrouter.py — D327: Unified OpenRouter cloud inference.

Consolidates openrouter_reasoner.py into one module.
This is the ONLY file that knows about OpenRouter mechanics.
D329: Also handles all cloud routing — no separate Anthropic direct path.

Phase 1: re-exports from original location (migration shim).
Phase 1b: actual code moves here, old file becomes thin redirect.

Public API (what the gateway calls):
  - OpenRouterReasoner class (interactive cloud reasoning)
  - preparse_via_openrouter(user_input, habits, model) → CSB str
"""

from __future__ import annotations

# ── From openrouter_reasoner ───────────────────────────────────────────────
from .reasoners.openrouter_reasoner import (
    OpenRouterReasoner,
    preparse_via_openrouter,
    MODEL_ALIASES,
)

__all__ = [
    "OpenRouterReasoner",
    "preparse_via_openrouter",
    "MODEL_ALIASES",
]

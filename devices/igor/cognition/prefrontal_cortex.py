"""
Prefrontal Cortex - executive reasoning.
Delegates to whichever reasoner is currently active.
The reasoner is pluggable: Anthropic API, browser-based AI, local model, or none (pure habits).

Judgment functions (assess_valence, measure_friction, calculate_roi) live in judgments.py (#74).
Re-exported here for backward compatibility.
"""

from ..memory.models import Memory
from .reasoners.base import BaseReasoner
from .judgments import (  # noqa: F401  (re-export)
    assess_valence,
    measure_friction,
    calculate_roi,
    _log_judgment,
    _embed_anchors,
    _POSITIVE_ANCHORS,
    _NEGATIVE_ANCHORS,
)


def reason(
    user_input: str,
    relevant_memories: list[Memory],
    core_patterns: list[Memory],
    instance_id: str,
    reasoner: BaseReasoner = None,
    cortex=None,
) -> tuple[str, float]:
    """
    Call the active reasoner. Returns (response_text, cost).
    cortex is forwarded to the reasoner so it can inject ring context directly.
    If no reasoner is provided, uses Anthropic by default.
    """
    if reasoner is None:
        from .reasoners.anthropic import AnthropicReasoner
        reasoner = AnthropicReasoner()

    return reasoner.reason(user_input, relevant_memories, core_patterns, instance_id, cortex=cortex)

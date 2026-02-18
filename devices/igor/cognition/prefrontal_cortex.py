"""
Prefrontal Cortex - executive reasoning.
Delegates to whichever reasoner is currently active.
The reasoner is pluggable: Anthropic API, browser-based AI, local model, or none (pure habits).
"""

from ..memory.models import Memory
from .reasoners.base import BaseReasoner


def reason(
    user_input: str,
    relevant_memories: list[Memory],
    core_patterns: list[Memory],
    instance_id: str,
    reasoner: BaseReasoner = None,
) -> tuple[str, float]:
    """
    Call the active reasoner. Returns (response_text, cost).
    If no reasoner is provided, uses Anthropic by default.
    Eventually: when habits cover everything, this won't be called at all.
    """
    if reasoner is None:
        from .reasoners.anthropic import AnthropicReasoner
        reasoner = AnthropicReasoner()

    return reasoner.reason(user_input, relevant_memories, core_patterns, instance_id)


def assess_valence(interaction_text: str, response_text: str) -> float:
    positive = ["thank", "great", "excellent", "perfect", "yes", "good", "love", "appreciate"]
    negative = ["wrong", "error", "fail", "bad", "incorrect", "frustrat", "annoyed"]
    combined = (interaction_text + " " + response_text).lower()
    pos = sum(1 for s in positive if s in combined)
    neg = sum(1 for s in negative if s in combined)
    if pos + neg == 0:
        return 0.3
    return max(-1.0, min(1.0, (pos - neg) / (pos + neg)))


def measure_friction(used_api: bool, retry_count: int = 0, tool_failures: int = 0) -> float:
    friction = 0.0
    if used_api:
        friction += 0.25
    friction += retry_count * 0.10
    friction += tool_failures * 0.15
    return min(1.0, friction)


def calculate_roi(goal_achieved: bool, new_learning: bool, used_api: bool) -> float:
    value = 0.5 if goal_achieved else 0.0
    value += 0.3 if new_learning else 0.0
    effort = 0.2 if used_api else 0.05
    return max(-1.0, min(1.0, value - effort))

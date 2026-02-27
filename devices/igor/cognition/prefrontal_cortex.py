"""
Prefrontal Cortex - executive reasoning.
Delegates to whichever reasoner is currently active.
The reasoner is pluggable: Anthropic API, browser-based AI, local model, or none (pure habits).

Judgment functions (assess_valence, measure_friction, calculate_roi) now log their
reasoning to ring_memory so decisions are auditable, not just their numeric outputs.
"""

from ..memory.models import Memory
from .reasoners.base import BaseReasoner


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


def _log_judgment(cortex, judgment_type: str, inputs: dict, result, reasoning: str):
    """
    Write a judgment record to ring_memory.
    cortex may be None (if caller doesn't have one) — in that case, skip silently.
    """
    if cortex is None:
        return
    entry = (
        f"JUDGMENT:{judgment_type} | "
        f"inputs={inputs} | "
        f"result={result} | "
        f"why={reasoning}"
    )
    cortex.write_ring(entry, category="judgment")


def assess_valence(interaction_text: str, response_text: str, cortex=None) -> float:
    """
    Score the emotional valence of an interaction.
    Returns float in [-1.0, 1.0]. Neutral default is 0.3 (slightly positive).
    Logs its reasoning if a cortex is provided.
    """
    positive = ["thank", "great", "excellent", "perfect", "yes", "good", "love", "appreciate"]
    negative = ["wrong", "error", "fail", "bad", "incorrect", "frustrat", "annoyed"]
    combined = (interaction_text + " " + response_text).lower()

    pos_hits = [s for s in positive if s in combined]
    neg_hits = [s for s in negative if s in combined]
    pos = len(pos_hits)
    neg = len(neg_hits)

    if pos + neg == 0:
        result = 0.3
        reasoning = "no signal words found → neutral default (0.3)"
    else:
        result = max(-1.0, min(1.0, (pos - neg) / (pos + neg)))
        reasoning = f"pos_hits={pos_hits} neg_hits={neg_hits} → ({pos}-{neg})/({pos}+{neg})={result:.2f}"

    _log_judgment(cortex, "valence", {
        "input_len": len(interaction_text),
        "response_len": len(response_text),
    }, result, reasoning)

    return result


def measure_friction(
    used_api: bool,
    retry_count: int = 0,
    tool_failures: int = 0,
    cortex=None,
) -> float:
    """
    Score how much friction this interaction generated.
    Returns float in [0.0, 1.0].
    Logs its reasoning if a cortex is provided.
    """
    components = []
    friction = 0.0

    if used_api:
        friction += 0.25
        components.append("api_call(+0.25)")
    friction += retry_count * 0.10
    if retry_count:
        components.append(f"retries={retry_count}(+{retry_count * 0.10:.2f})")
    friction += tool_failures * 0.15
    if tool_failures:
        components.append(f"tool_failures={tool_failures}(+{tool_failures * 0.15:.2f})")

    result = min(1.0, friction)
    reasoning = " + ".join(components) if components else "no friction sources"
    if result < friction:
        reasoning += " [capped at 1.0]"

    _log_judgment(cortex, "friction", {
        "used_api": used_api,
        "retry_count": retry_count,
        "tool_failures": tool_failures,
    }, result, reasoning)

    return result


def calculate_roi(
    goal_achieved: bool,
    new_learning: bool,
    used_api: bool,
    cortex=None,
) -> float:
    """
    Score the return-on-investment for this interaction.
    Returns float in [-1.0, 1.0].
    Logs its reasoning if a cortex is provided.
    """
    value_parts = []
    effort_parts = []

    value = 0.5 if goal_achieved else 0.0
    if goal_achieved:
        value_parts.append("goal_achieved(+0.5)")

    value += 0.3 if new_learning else 0.0
    if new_learning:
        value_parts.append("new_learning(+0.3)")

    effort = 0.2 if used_api else 0.05
    effort_parts.append(f"{'api' if used_api else 'no_api'}({'0.20' if used_api else '0.05'})")

    result = max(-1.0, min(1.0, value - effort))
    reasoning = (
        f"value=[{', '.join(value_parts) or 'none'}] "
        f"effort=[{', '.join(effort_parts)}] "
        f"→ {value:.2f} - {effort:.2f} = {result:.2f}"
    )

    _log_judgment(cortex, "roi", {
        "goal_achieved": goal_achieved,
        "new_learning": new_learning,
        "used_api": used_api,
    }, result, reasoning)

    return result

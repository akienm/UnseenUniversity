"""
Multi-upstream query support (change.40).

Allows querying multiple reasoning models simultaneously and comparing their responses.
Used by the /upstream command in main.py.
"""

from ..memory.models import Memory


def query_multiple(
    user_input: str,
    relevant_memories: list[Memory],
    core_patterns: list[Memory],
    instance_id: str,
    reasoners: dict,
    cortex=None,
) -> list[tuple[str, str, float]]:
    """
    Send user_input to all named reasoners.
    reasoners: dict of {name: BaseReasoner}
    Returns list of (model_name, response_text, cost_usd).
    Runs sequentially (not async — safe with all current backends).
    """
    results = []
    for name, reasoner in reasoners.items():
        try:
            kwargs = {"cortex": cortex} if hasattr(reasoner.reason, "__code__") and "cortex" in reasoner.reason.__code__.co_varnames else {}
            text, cost = reasoner.reason(
                user_input, relevant_memories, core_patterns, instance_id, **kwargs
            )
            results.append((name, text, cost))
        except Exception as e:
            results.append((name, f"[Error: {e}]", 0.0))
    return results


def compare_responses(responses: list[tuple[str, str, float]]) -> str:
    """
    Compare multiple model responses.
    Uses local Ollama to identify agreements/disagreements.
    Falls back to formatted side-by-side if Ollama unavailable.
    """
    if not responses:
        return "(no responses to compare)"

    lines = [f"Multi-upstream comparison ({len(responses)} models):"]
    for name, text, cost in responses:
        cost_str = f"${cost:.4f}" if cost > 0 else "free"
        lines.append(f"\n── {name} ({cost_str}) ──")
        lines.append(text[:500])
    plain = "\n".join(lines)

    # Try KoboldCpp synthesis
    try:
        from .reasoners.koboldcpp_reasoner import _post_json, DEFAULT_HOST, CHAT_ENDPOINT
        import os as _os
        host = _os.getenv("KOBOLDCPP_HOST", DEFAULT_HOST)
        prompt_parts = [
            "Compare these AI responses to the same question. "
            "Identify: (1) what they agree on, (2) key differences, (3) which is most useful. "
            "Be very brief.\n\n",
        ]
        for name, text, _ in responses:
            prompt_parts.append(f"{name}:\n{text[:300]}\n\n")
        prompt = "".join(prompt_parts)
        payload = {"messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 200, "temperature": 0.2}
        data = _post_json(host, CHAT_ENDPOINT, payload, timeout=30)
        synthesis = data["choices"][0]["message"]["content"].strip()
        return plain + f"\n\n── Synthesis (local) ──\n{synthesis}"
    except Exception:
        return plain

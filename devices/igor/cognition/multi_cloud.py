"""
Multi-cloud inference query support (change.40).

Allows querying multiple cloud reasoning models simultaneously and comparing their responses.
Used by the /cloud command in main.py.
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
    Compare multiple cloud inference model responses.
    Uses local Ollama to identify agreements/disagreements.
    Falls back to formatted side-by-side if Ollama unavailable.
    """
    if not responses:
        return "(no responses to compare)"

    lines = [f"Multi-cloud inference comparison ({len(responses)} models):"]
    for name, text, cost in responses:
        cost_str = f"${cost:.4f}" if cost > 0 else "free"
        lines.append(f"\n── {name} ({cost_str}) ──")
        lines.append(text[:500])
    plain = "\n".join(lines)

    # Try Ollama synthesis (local inference)
    try:
        import ollama as _ollama
        from .reasoners.ollama_reasoner import OLLAMA_LOCAL_MODEL
        prompt_parts = [
            "Compare these AI responses to the same question. "
            "Identify: (1) what they agree on, (2) key differences, (3) which is most useful. "
            "Be very brief.\n\n",
        ]
        for name, text, _ in responses:
            prompt_parts.append(f"{name}:\n{text[:300]}\n\n")
        prompt = "".join(prompt_parts)
        resp = _ollama.chat(
            model=OLLAMA_LOCAL_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 200},
        )
        synthesis = (resp["message"]["content"] if isinstance(resp, dict) else resp.message.content).strip()
        return plain + f"\n\n── Synthesis (local inference) ──\n{synthesis}"
    except Exception:
        return plain

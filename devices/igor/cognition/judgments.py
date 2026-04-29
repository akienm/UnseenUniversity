import logging
from ..igor_base import get_logger

"""
Judgment functions — assess_valence, measure_friction, calculate_roi.

Extracted from prefrontal_cortex.py (#74) to keep pfc focused on reasoner delegation.
These are amygdala/cingulate-analog scoring functions: they take interaction signals
and return scalar scores used to update the milieu.
"""


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


# ── Valence anchor sentences (embedded once, cached to disk) ─────────────────
_POSITIVE_ANCHORS = [
    "This is excellent, exactly what I needed, thank you",
    "That worked perfectly, great job",
    "I really appreciate this, very helpful and clear",
    "Yes, this is right, I'm happy with this outcome",
]
_NEGATIVE_ANCHORS = [
    "This is wrong and frustrating, that didn't work",
    "I'm annoyed, this is broken and incorrect",
    "That's not what I wanted at all, this is bad",
    "This failed, I'm stuck and this isn't helping",
]


def _embed_anchors():
    """Return (pos_vecs, neg_vecs) — cached via embedder file cache."""
    try:
        from .embedder import embed

        pos = [v for t in _POSITIVE_ANCHORS if (v := embed(t)) is not None]
        neg = [v for t in _NEGATIVE_ANCHORS if (v := embed(t)) is not None]
        return pos, neg
    except Exception:
        return [], []


def assess_valence(interaction_text: str, response_text: str, cortex=None) -> float:
    """
    Score the emotional valence of an interaction.
    Returns float in [-1.0, 1.0]. Neutral default is 0.3 (slightly positive).

    #94: Uses nomic-embed-text semantic similarity against positive/negative anchor
    sentences. Falls back to keyword matching if embeddings are unavailable.
    Logs its reasoning if a cortex is provided.
    """
    combined = ((interaction_text or "") + " " + (response_text or "")).lower()

    # ── Semantic path (preferred) ─────────────────────────────────────────────
    try:
        from .embedder import embed, cosine_similarity

        text_vec = embed(combined[:500])  # cap to avoid huge embed calls
        if text_vec is not None:
            pos_vecs, neg_vecs = _embed_anchors()
            if pos_vecs and neg_vecs:
                pos_sim = max(cosine_similarity(text_vec, v) for v in pos_vecs)
                neg_sim = max(cosine_similarity(text_vec, v) for v in neg_vecs)
                # Scale: similarity difference → [-1, 1]; shift +0.1 (Igor is usually helpful)
                result = max(-1.0, min(1.0, (pos_sim - neg_sim) * 2.0 + 0.1))
                reasoning = f"embedding|pos_sim={pos_sim:.3f}|neg_sim={neg_sim:.3f}|result={result:.2f}"
                _log_judgment(
                    cortex,
                    "valence",
                    {
                        "method": "embedding",
                        "input_len": len(interaction_text),
                    },
                    result,
                    reasoning,
                )
                return result
    except Exception as _bare_e:
        get_logger(__name__).warning(
            "bare except in wild_igor/igor/cognition/judgments.py: %s", _bare_e
        )

    # ── Keyword fallback ──────────────────────────────────────────────────────
    positive = [
        "thank",
        "great",
        "excellent",
        "perfect",
        "yes",
        "good",
        "love",
        "appreciate",
    ]
    negative = ["wrong", "error", "fail", "bad", "incorrect", "frustrat", "annoyed"]

    pos_hits = [s for s in positive if s in combined]
    neg_hits = [s for s in negative if s in combined]
    pos = len(pos_hits)
    neg = len(neg_hits)

    if pos + neg == 0:
        result = 0.3
        reasoning = "keyword_fallback|no signal words → neutral (0.3)"
    else:
        result = max(-1.0, min(1.0, (pos - neg) / (pos + neg)))
        reasoning = (
            f"keyword_fallback|pos={pos_hits}|neg={neg_hits}|result={result:.2f}"
        )

    _log_judgment(
        cortex,
        "valence",
        {
            "method": "keyword",
            "input_len": len(interaction_text),
            "response_len": len(response_text),
        },
        result,
        reasoning,
    )

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

    _log_judgment(
        cortex,
        "friction",
        {
            "used_api": used_api,
            "retry_count": retry_count,
            "tool_failures": tool_failures,
        },
        result,
        reasoning,
    )

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
    effort_parts.append(
        f"{'api' if used_api else 'no_api'}({'0.20' if used_api else '0.05'})"
    )

    result = max(-1.0, min(1.0, value - effort))
    reasoning = (
        f"value=[{', '.join(value_parts) or 'none'}] "
        f"effort=[{', '.join(effort_parts)}] "
        f"→ {value:.2f} - {effort:.2f} = {result:.2f}"
    )

    _log_judgment(
        cortex,
        "roi",
        {
            "goal_achieved": goal_achieved,
            "new_learning": new_learning,
            "used_api": used_api,
        },
        result,
        reasoning,
    )

    return result

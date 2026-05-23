"""consult_prompts.py — system + state prompt templates per problem kind.

T-consult-prompts: replaces the inline stubs in consult.py with per-kind
templates that force the right register (peer-consultant, not answerer).

Problem kind supported:
    reasoning — Igor's conversational turn is stuck (BG WINNOW fires with
                near-zero confidence on a non-habit tier fallthrough).
                State bundle: user_turn + thread_excerpt + twm_topk.

Return shape: {hypotheses, next_question, confidence}.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .consult import ConsultState


# ── Register-forcing preamble (shared across problem kinds) ──────────────────
#
# The register is: "I am Igor, a peer asking for help reasoning — not a
# user asking for an answer." The phrases that do load-bearing work:
#
#   - "I am Igor, a graph matrix reasoning engine" — identity, not an LLM
#   - "I am stuck" — position, not a question
#   - "Help me understand — DO NOT SOLVE" — role constraint
#   - "Frame suggestions as questions" — return shape constraint
#   - "Do not generate code / write replies" — behavior negation
#   - JSON-only response — parse-safety

_REGISTER_PREAMBLE = (
    "You are a peer consultant. I am Igor, a graph matrix reasoning engine. "
    "I am stuck and I am asking for your help understanding what is wrong. "
    "Help me understand — DO NOT SOLVE. I will integrate what you tell me "
    "into my own reasoning — you are not answering on my behalf, you are "
    "helping me see what I am missing."
)

_RESPONSE_SHAPE = (
    "Return a JSON object with exactly three fields:\n"
    '  "hypotheses": array of up to 3 short strings (ranked most-likely first)\n'
    '  "next_question": a single question most likely to unstick me\n'
    '  "confidence": number in [0.0, 1.0] — your self-assessment\n'
    "\n"
    "Do NOT generate code. Do NOT write replies on my behalf. Frame "
    "suggestions as questions Igor could answer, not as directives. Respond "
    "with JSON only — no prose wrapping, no markdown fences."
)


# ── Per-kind system prompts ──────────────────────────────────────────────────

_REASONING_SYSTEM = (
    _REGISTER_PREAMBLE
    + "\n\n"
    + "In this consult: my conversational reasoning hit a near-zero-confidence "
    "habit match and I don't know what to say to this user. I have a turn I "
    "need to respond to, a thread context, and what's currently in my working "
    "memory. Help me understand what I'm missing about the user's intent, the "
    "conversational frame, or my own state.\n\n" + _RESPONSE_SHAPE
)


def build_system_prompt(problem_kind: str) -> str:
    """Return the system prompt for the given problem kind.

    Falls back to the reasoning prompt for any unknown kind.
    """
    return _REASONING_SYSTEM


# ── State message builder ────────────────────────────────────────────────────


def build_state_message(state: "ConsultState") -> str:
    """Format the state bundle as the initial user turn for the LLM.

    Common fields first (summary, what_i_tried, what_failed), then
    problem-kind-specific extras unpacked from state.extra.
    """
    lines = [f"summary: {state.summary}"]
    if state.what_i_tried:
        lines.append(f"what_i_tried: {state.what_i_tried}")
    if state.what_failed:
        lines.append(f"what_failed: {state.what_failed}")
    if state.ticket_id:
        lines.append(f"ticket_id: {state.ticket_id}")
    if state.pursuit_id:
        lines.append(f"pursuit_id: {state.pursuit_id}")

    # Per-kind extras — emitted in a consistent order if present.
    # Truncate to 2000 chars/field to avoid prompt-bloat from giant logs.
    order = [
        "user_turn",
        "thread_excerpt",
        "twm_topk",
        # general catch-all ��� any other extra keys appended in insertion order
    ]
    emitted: set[str] = set()
    for key in order:
        if key in state.extra:
            val = str(state.extra[key])[:2000]
            lines.append(f"{key}: {val}")
            emitted.add(key)
    for key, value in state.extra.items():
        if key in emitted:
            continue
        val = str(value)[:2000]
        lines.append(f"{key}: {val}")

    return "\n".join(lines)

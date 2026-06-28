"""prompt_builder — canonical home for the six LLM prompt reliability patterns.

Six patterns that govern whether OR/Anthropic models follow instructions reliably.
Discovered via DickSimnel's toolloop (D-six-prompt-patterns-base-class-2026-06-13).
Import from here instead of copy-pasting per-device.

Patterns:
  1. Exit protocol at top — terminal response format declared first in system prompt
  2. Temperature = 0 declared explicitly — build_inference_request() enforces the default
  3. Consequence framing — rules name what happens on violation ("triggers re-dispatch")
  4. Imperative register — "must", "output X", not "you may", "feel free"
  5. Tool-first enforcement — tool_choice="required" on turn 1 of a tool loop
  6. Failure-mode naming — named error classes (ESCALATE, COST_EXCEEDED) not generic prose
"""

from __future__ import annotations

# ── Pattern 1: Exit protocol ──────────────────────────────────────────────────

EXIT_PROTOCOL = """\
## Exit protocol

When finished, respond ONLY with (no prose, no other text):
{"status": "done", "result": "<one-line summary of what was done>", "error_class": null, "error_number": null}

When escalating, respond ONLY with:
{"status": "escalate", "result": "<reason>", "error_class": "ESCALATE", "error_number": null}

No other non-tool responses are valid. Any other text is an error."""

# ── Pattern 6: Failure-mode naming ───────────────────────────────────────────


class FailureClass:
    """Named error classes for system prompt failure-mode naming (pattern 6).

    Use these constants in system prompts so model output is parseable without regex:
        f"If scope is unclear: output ESCALATE: <reason>"
    """
    ESCALATE = "ESCALATE"
    COST_EXCEEDED = "COST_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"


# ── Pattern 5: Tool-first enforcement ────────────────────────────────────────

def tool_choice_for_turn(turn: int) -> dict:
    """Return {"tool_choice": "required"} on turn 0, {} on subsequent turns.

    Pattern 5: forces a tool call on turn 1, preventing planning-mode prose narration
    that wastes token budget before the model takes any action.
    """
    return {"tool_choice": "required"} if turn == 0 else {}


# ── Pattern 2: Temperature = 0 ───────────────────────────────────────────────

def build_inference_request(*, model, messages, system, tools=None, **kwargs):
    """Build an InferenceRequest with temperature=0.0 as the explicit default.

    Pattern 2: temperature must be declared explicitly, not left to provider default.
    Determinism is not implied by the task; callers that want creative responses must
    override explicitly: build_inference_request(..., temperature=1.0).

    Patterns 3+4 (consequence framing, imperative register) live in the system prompt
    text — enforce via /audit-precode, not runtime code.
    """
    from unseen_university.devices.inference.device import InferenceRequest

    kwargs.setdefault("temperature", 0.0)
    return InferenceRequest(
        model=model,
        messages=messages,
        system=system,
        tools=tools,
        **kwargs,
    )

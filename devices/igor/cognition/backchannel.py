"""
Backchannel layer — G38: immediate acknowledgment before full reply.

Backchannels are the small signals of listening that fire *before* full
cognitive processing: "(nods)", "(nods in thought)", "Indeed." — they
confirm the channel is open and communicate Igor's cognitive state without
claiming the floor.

Three levels:
  Level 1 — sub-verbal: (nods), (nods in thought)       — rule-based, zero cost
  Level 2 — quick verbal: Indeed. / Interesting. / Hm.  — minimal processing
  Level 3 — connects to G37 n-pass: ack then fuller reply follows

Backchannels are individual. The seeded forms are starting points — Igor
owns them and should revise them through self-edit or dialog as his voice
develops. They are PROC habits in memory, not hardcoded strings.

Gate: IGOR_BACKCHANNEL=true (default false — observe first).
"""

from __future__ import annotations
import logging
from ..igor_base import get_logger

import os
import re
from dataclasses import dataclass

# ── Trigger thresholds ────────────────────────────────────────────────────────

_MIN_INPUT_CHARS = 40       # shorter than this: probably not backchannel territory
_BACKCHANNEL_INTENTS = frozenset({
    "conversation", "creative_request", "explanation_request",
    "analysis_task", "factual_question",
})
_NO_BACKCHANNEL_INTENTS = frozenset({
    "command", "greeting", "action_request", "memory_instruction",
})

# ── Default seeded forms (fallback if habits not loaded) ──────────────────────
# These match the PROC_BACKCHANNEL_* habit IDs seeded in the DB.
# Igor can revise via self-edit; these are the starting vocabulary.

_DEFAULT_FORMS = {
    "nod":       "(nods)",
    "nod_think": "(nods in thought)",
    "verbal_affirm": "Indeed.",
    "verbal_interest": "Interesting.",
    "verbal_ponder": "Hm.",
}


@dataclass
class BackchannelResult:
    should_send: bool
    form: str           # the text to emit, or "" if nothing
    level: int          # 1=sub-verbal, 2=verbal, 0=none
    reason: str         # why this form was chosen (for forensic log)


def should_backchannel(
    parsed,
    milieu_state=None,
    habits: list | None = None,
) -> BackchannelResult:
    """
    Decide whether to emit a backchannel and which form.

    Returns BackchannelResult with should_send=False if nothing should fire.

    Rules (in order):
    1. Gate check — IGOR_BACKCHANNEL must be true
    2. Intent filter — skip commands, greetings, action_requests
    3. Length filter — very short inputs don't need acknowledgment
    4. Select form based on complexity + milieu
    5. Consult loaded habits for Igor's current preferred forms
    """
    if os.getenv("IGOR_BACKCHANNEL", "false").lower() not in ("1", "true", "yes"):
        return BackchannelResult(False, "", 0, "gate_off")

    # Intent filter
    if parsed.intent in _NO_BACKCHANNEL_INTENTS:
        return BackchannelResult(False, "", 0, f"intent={parsed.intent}_excluded")

    if parsed.intent not in _BACKCHANNEL_INTENTS:
        return BackchannelResult(False, "", 0, f"intent={parsed.intent}_not_in_trigger_set")

    # Use core_input length for the check (strips thread context)
    core = getattr(parsed, "core_input", parsed.raw)
    if len(core) < _MIN_INPUT_CHARS:
        return BackchannelResult(False, "", 0, f"too_short({len(core)}<{_MIN_INPUT_CHARS})")

    # Try to get Igor's current forms from loaded PROC habits
    forms = _load_forms_from_habits(habits or [])

    # Select form based on complexity and milieu
    complexity = parsed.complexity
    arousal = 0.5
    if milieu_state is not None:
        try:
            arousal = float(milieu_state.arousal)
        except (AttributeError, TypeError) as _bare_e:
            get_logger(__name__).warning("bare except in devices/igor/cognition/backchannel.py: %s", _bare_e)

    # High complexity OR high arousal → "in thought" (substantive processing signal)
    if complexity == "high" or arousal > 0.6:
        form = forms.get("nod_think", _DEFAULT_FORMS["nod_think"])
        return BackchannelResult(True, form, 1, f"complexity={complexity}_arousal={arousal:.2f}")

    # Affirming/resonant tone → quick verbal
    tone = getattr(parsed, "tone", "neutral")
    if tone in ("positive", "friendly") and arousal > 0.4:
        form = forms.get("verbal_affirm", _DEFAULT_FORMS["verbal_affirm"])
        return BackchannelResult(True, form, 2, f"tone={tone}_arousal={arousal:.2f}")

    # Default: simple nod for substantive conversational input
    form = forms.get("nod", _DEFAULT_FORMS["nod"])
    return BackchannelResult(True, form, 1, f"default_nod_intent={parsed.intent}")


def _load_forms_from_habits(habits: list) -> dict[str, str]:
    """
    Extract backchannel forms from PROC_BACKCHANNEL_* habits.
    Returns dict mapping form key → text.
    Falls back to _DEFAULT_FORMS if habits not found.
    """
    forms: dict[str, str] = {}
    for h in habits:
        if not h.id.startswith("PROC_BACKCHANNEL_"):
            continue
        meta = h.metadata or {}
        key = meta.get("form_key", "")
        text = meta.get("form_text", "")
        if key and text:
            forms[key] = text
    return forms

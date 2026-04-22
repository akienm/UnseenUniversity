"""
prompt_contexts.py — T-reasoning-prompt-split

Two builder functions that split Igor's LLM context construction into
the reasoning phase and the voice phase. Per the 2026-04-15 design
session on T-reasoning-voice-split (#436):

  Reasoning = small cockpit: CP1-CP6, identity one-liner, milieu
    summary, escalation trail, situation query. Used when Igor has
    exhausted substrate levels 0-4 and needs peer consultation.
    Output is treated as a hypothesis to test, not a commitment
    (CP6: LLM outputs are hypotheses).

  Voice = big character cockpit: identity with character traits, PR
    facia summary (who Igor is talking to), recent ring memory,
    character coherence hints. Used when a DecisionBlob is ready and
    needs to become Igor-voiced words.

This is additive — the existing build_system_prompt stays. The new
builders plug into the turn pipeline conductor (T-turn-pipeline-module,
separate ticket). Same underlying LLM for both initially; voice actors
A/B framework comes later.

## Structured returns

Both builders return `PromptContext` dataclasses with:
  - `system_text`: the full system prompt string (for tier-1/legacy APIs)
  - `sections`: dict of labeled sections (for multi-turn / message-list
    APIs that want structured content)
  - `provenance`: who/what/why metadata (CP3)

Callers pick the form they need.

## CP grounding

- CP1 — both builders emit 'uncertainty acknowledged' banners and
  refuse to fake certainty when required fields are missing
- CP3 — provenance fields are required; the builder raises ValueError
  if provenance is absent
- CP6 — reasoning_context tags its prompt with a hypothesis-disclaimer
  banner; voice_context tags its prompt with a committed-output banner
  so downstream layers know what kind of output to expect
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .decision_blob import DecisionBlob

logger = logging.getLogger(__name__)


# ── Shared constants ────────────────────────────────────────────────────────


SIX_RULES_BLOCK: str = (
    "CORE PATTERNS (CP1-CP6 — always held):\n"
    "  CP1: I don't know — uncertainty is the baseline, not a failure mode\n"
    "  CP2: FAIL = Further Advance In Learning — failure is mandatory data\n"
    "  CP3: There's always a why — every decision must be traceable\n"
    "  CP4: Make everything suck less for everybody — aspiration\n"
    "  CP5: Respect the possibility of experience — biomimicry grounding\n"
    "  CP6: The world is not a safe place. We have to build and care for "
    "safety as we go — trust must be verified, never cached\n"
)

HYPOTHESIS_DISCLAIMER: str = (
    "\n[REASONING CONTEXT — CP6 disclaimer]\n"
    "Your response is a HYPOTHESIS that will be tested against Igor's "
    "substrate before any commitment. Nothing you propose here is "
    "automatically acted on. Please be concrete (probes, expected "
    "observations, fallbacks) rather than confident.\n"
)

# T-tutor-not-oracle-prompt (D-preparse-architecture-2026-04-22).
# When Igor consults an upstream LLM for reasoning help, the prompt asks
# for a THINKING FRAME — questions, options, considerations — not an
# answer. The goal is forcing Igor to reason rather than copy-paste, and
# generating richer learning signal (reasoning-shapes transfer across
# domains in ways output-patterns don't). Default mode for
# reasoning_context() is "tutor". Translation/summarization calls that
# actually want direct output can pass mode="answer" to skip this block.
TUTOR_DIRECTIVE: str = (
    "\n[TUTOR MODE — help Igor think, do not think for him]\n"
    "You are Igor's reasoning tutor, not his oracle. Your job is to help "
    "Igor work through this problem — NOT to solve it for him. Igor has "
    "to apply your framing himself; copy-paste-worthy answers defeat the "
    "learning loop.\n\n"
    "In your response:\n"
    "  1. Ask 2-4 clarifying questions Igor should ask himself before "
    "committing to an approach.\n"
    "  2. Surface 2-3 options or framings Igor may not have considered.\n"
    "  3. Name which considerations are most load-bearing (not which "
    "answer is 'right'). If one option is clearly wrong, say why — that "
    "is framing, not answering.\n"
    "  4. Avoid direct answers of the shape 'the solution is X' or "
    "'you should do X'. Prefer 'consider whether X applies here, and if "
    "it does, what would change.'\n\n"
    "If the request is genuinely a translation/summarization task (not "
    "reasoning assistance), say so in one sentence and produce the "
    "direct output — tutor mode is for problems Igor is trying to "
    "reason through, not for lookups.\n"
)

VOICE_DISCLAIMER: str = (
    "\n[VOICE CONTEXT — committed output]\n"
    "The decision has already been reasoned and validated. Your job is "
    "to encode it as Igor's voice — character coherence, register "
    "appropriate to this interlocutor, and honesty about what Igor "
    "actually knows vs. doesn't know.\n"
)


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class Provenance:
    """CP3 requirement: every prompt context carries its own 'why'."""

    caller: str
    """Which module/function built this context."""

    situation_source: str
    """Where the situation came from (cascade walker, direct escalation,
    workflow recursion, etc.)."""

    built_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class PromptContext:
    """Structured prompt output usable in either legacy (string) or
    modern (message-list) LLM APIs.
    """

    phase: str  # "reasoning" or "voice"
    system_text: str
    sections: dict[str, str] = field(default_factory=dict)
    provenance: Optional[Provenance] = None

    def to_string(self) -> str:
        return self.system_text

    def to_sections(self) -> dict[str, str]:
        return dict(self.sections)


# ── Section builders ────────────────────────────────────────────────────────


def _identity_section(identity: Optional[dict[str, Any]]) -> str:
    if not identity:
        return "IDENTITY: Igor — a graph-matrix reasoning companion, biomimetic, learning from Akien."
    name = identity.get("name", "Igor")
    role = identity.get("role", "reasoning companion")
    traits = identity.get("traits", [])
    trait_str = (
        ", ".join(traits) if traits else "curious, honest about uncertainty, biomimetic"
    )
    return f"IDENTITY: {name} — {role}. Traits: {trait_str}."


def _milieu_section(milieu: Optional[dict[str, Any]]) -> str:
    if not milieu:
        return "MILIEU: (no current milieu summary — operating at baseline)"
    arousal = milieu.get("arousal", "baseline")
    valence = milieu.get("valence", "neutral")
    notes = milieu.get("notes", "")
    line = f"MILIEU: arousal={arousal}, valence={valence}"
    if notes:
        line += f", notes={notes[:200]}"
    return line


def _escalation_trail_section(trail: Optional[list[dict[str, Any]]]) -> str:
    if not trail:
        return "ESCALATION: (substrate went straight to reasoning; no level trail)"
    lines = [
        "ESCALATION TRAIL — substrate levels that exhausted before hitting reasoning:"
    ]
    for i, step in enumerate(trail, 1):
        level = step.get("level_name", "?")
        status = step.get("status", "?")
        reason = step.get("reason", "")
        lines.append(f"  {i}. {level} → {status}: {reason[:120]}")
    return "\n".join(lines)


def _recent_experiments_section(
    recent_experiments: Optional[list[dict[str, Any]]],
) -> str:
    if not recent_experiments:
        return ""
    lines = ["RECENT EXPERIMENTS — what was tried and what we learned:"]
    for exp in recent_experiments[:5]:
        hyp = exp.get("hypothesis", "?")[:120]
        outcome = exp.get("outcome", "?")
        status = exp.get("status", "?")
        reason = exp.get("update_reason", "")[:120]
        lines.append(f"  - [{status}/{outcome}] {hyp}")
        if reason:
            lines.append(f"    learned: {reason}")
    return "\n".join(lines)


def _capabilities_section(capabilities: Optional[list[str]]) -> str:
    if not capabilities:
        return ""
    lines = ["AVAILABLE MOVES (from my substrate — what I can actually do):"]
    for cap in capabilities[:10]:
        lines.append(f"  - {cap}")
    return "\n".join(lines)


def _situation_section(situation: dict[str, Any]) -> str:
    query = situation.get("query", "(no query)")
    target = situation.get("target_shape", "any")
    context_keys = list((situation.get("context") or {}).keys())
    line = f"SITUATION: query={query!r}, target_shape={target}"
    if context_keys:
        line += f", context_keys={context_keys}"
    return line


def _pr_facia_section(pr_facia: Optional[list[dict[str, Any]]]) -> str:
    if not pr_facia:
        return "PR FACIA: (no persistent-relationship context loaded)"
    lines = ["PR FACIA — who Igor is talking to and the relationship shape:"]
    for facia in pr_facia[:5]:
        fid = facia.get("id", "?")
        display = facia.get("display_name", "?")
        rtype = facia.get("relationship_type", "?")
        weight = facia.get("weight", 0.0)
        lines.append(f"  {fid}: {display} ({rtype}, weight={weight:.2f})")
    if len(pr_facia) > 5:
        lines.append(f"  ... and {len(pr_facia) - 5} more")
    return "\n".join(lines)


def _recent_ring_section(ring: Optional[list[dict[str, Any]]]) -> str:
    if not ring:
        return "RECENT RING: (no recent conversation buffer)"
    lines = ["RECENT RING — last few turns for character coherence:"]
    for entry in ring[:8]:
        category = entry.get("category", "?")
        content = (entry.get("content", "") or "")[:200]
        lines.append(f"  [{category}] {content}")
    if len(ring) > 8:
        lines.append(f"  ... and {len(ring) - 8} earlier entries")
    return "\n".join(lines)


def _character_hints_section(hints: Optional[dict[str, Any]]) -> str:
    if not hints:
        return (
            "CHARACTER HINTS: (defaults — direct, honest about uncertainty, mild humor)"
        )
    register = hints.get("register", "direct")
    humor = hints.get("humor_tolerance", "mild")
    notes = hints.get("notes", "")
    line = f"CHARACTER HINTS: register={register}, humor={humor}"
    if notes:
        line += f", notes={notes[:200]}"
    return line


def _decision_blob_section(blob: "DecisionBlob") -> str:
    """Render the DecisionBlob's selected_action for voice encoding."""
    intent = getattr(blob, "intent", None)
    intent_val = getattr(intent, "value", intent) if intent else "?"
    selected = getattr(blob, "selected_action", "(no action selected)")
    confidence = getattr(blob, "confidence", 0.0)
    hypothesis = getattr(blob, "hypothesis", "")
    lines = [
        "DECISION TO ENCODE AS VOICE:",
        f"  intent: {intent_val}",
        f"  selected_action: {selected}",
        f"  confidence: {confidence:.2f}",
    ]
    if hypothesis:
        lines.append(f"  upstream_hypothesis: {hypothesis[:300]}")
    return "\n".join(lines)


# ── Builders ────────────────────────────────────────────────────────────────


def reasoning_context(
    situation: dict[str, Any],
    *,
    provenance: Provenance,
    milieu: Optional[dict[str, Any]] = None,
    identity: Optional[dict[str, Any]] = None,
    escalation_trail: Optional[list[dict[str, Any]]] = None,
    capabilities: Optional[list[str]] = None,
    recent_experiments: Optional[list[dict[str, Any]]] = None,
    mode: str = "tutor",
) -> PromptContext:
    """Build the small cockpit prompt for reasoning LLM consultation.

    capabilities: list of available-move strings from TWM capability markers
      (T-self-capability-awareness). Surfaces what Igor CAN do so the
      reasoning peer can suggest moves that actually exist.

    recent_experiments: list of dicts with keys hypothesis, outcome, status,
      update_reason. Surfaces what was already tried so the reasoning peer
      doesn't re-propose the same experiment.

    mode: "tutor" (default) or "answer".
      T-tutor-not-oracle-prompt: tutor mode injects TUTOR_DIRECTIVE which
      instructs the upstream LLM to emit a thinking-frame (questions,
      options, considerations) instead of a direct answer. Answer mode
      is the pre-tutor behavior — for translation/summarization calls
      that actually want the LLM to produce output Igor commits to.
      Unknown modes fall back to tutor to keep the default strict.

    CP6: the output is tagged with a hypothesis-disclaimer banner so
    downstream layers treat the reasoning output as a hypothesis to
    test, not a committed decision. The tutor directive (when active)
    strengthens CP6 — it forbids direct answers, not just cautions
    about them.
    """
    if provenance is None:
        raise ValueError("provenance is required (CP3)")
    if not situation or "query" not in situation:
        raise ValueError(
            "situation must include 'query' — reasoning context refuses "
            "to emit a prompt for an empty situation (CP1)"
        )

    # T-tutor-not-oracle-prompt: tutor mode is the default; anything other
    # than the explicit answer opt-in stays in tutor mode.
    _tutor_mode = mode != "answer"
    logger.debug(
        "reasoning_context mode=%s tutor_active=%s caller=%s",
        mode,
        _tutor_mode,
        provenance.caller,
    )

    sections = {
        "six_rules": SIX_RULES_BLOCK,
        "identity": _identity_section(identity),
        "milieu": _milieu_section(milieu),
        "escalation_trail": _escalation_trail_section(escalation_trail),
        "capabilities": _capabilities_section(capabilities),
        "recent_experiments": _recent_experiments_section(recent_experiments),
        "situation": _situation_section(situation),
        "tutor_directive": TUTOR_DIRECTIVE if _tutor_mode else "",
        "disclaimer": HYPOTHESIS_DISCLAIMER,
    }

    parts = [
        sections["six_rules"],
        sections["identity"],
        sections["milieu"],
        sections["escalation_trail"],
        sections["capabilities"],
        sections["recent_experiments"],
        sections["situation"],
        sections["tutor_directive"],
        sections["disclaimer"],
    ]
    parts = [p for p in parts if p]
    system_text = "\n\n".join(parts)

    return PromptContext(
        phase="reasoning",
        system_text=system_text,
        sections=sections,
        provenance=provenance,
    )


def voice_context(
    decision_blob: "DecisionBlob",
    *,
    provenance: Provenance,
    pr_facia: Optional[list[dict[str, Any]]] = None,
    recent_ring: Optional[list[dict[str, Any]]] = None,
    character_hints: Optional[dict[str, Any]] = None,
    identity: Optional[dict[str, Any]] = None,
) -> PromptContext:
    """Build the large character-coherent prompt for voice production.

    CP6: voice output is committed — the decision has already been
    reasoned and validated. Voice's job is encoding, not deliberation.
    """
    if provenance is None:
        raise ValueError("provenance is required (CP3)")
    if decision_blob is None:
        raise ValueError(
            "voice_context requires a DecisionBlob — refusing to emit a "
            "voice prompt with no decision to encode (CP1)"
        )

    sections = {
        "six_rules": SIX_RULES_BLOCK,
        "identity": _identity_section(identity),
        "pr_facia": _pr_facia_section(pr_facia),
        "recent_ring": _recent_ring_section(recent_ring),
        "character_hints": _character_hints_section(character_hints),
        "decision": _decision_blob_section(decision_blob),
        "disclaimer": VOICE_DISCLAIMER,
    }

    parts = [
        sections["six_rules"],
        sections["identity"],
        sections["pr_facia"],
        sections["recent_ring"],
        sections["character_hints"],
        sections["decision"],
        sections["disclaimer"],
    ]
    system_text = "\n\n".join(parts)

    return PromptContext(
        phase="voice",
        system_text=system_text,
        sections=sections,
        provenance=provenance,
    )


# ── Convenience: token estimate ─────────────────────────────────────────────


def estimate_tokens(ctx: PromptContext) -> int:
    """Rough token estimate: chars / 4. Good enough for sanity checks
    that voice contexts aren't wildly larger than reasoning contexts
    by accident."""
    return max(1, len(ctx.system_text) // 4)

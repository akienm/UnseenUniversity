"""
trust.py — Source trust scoring for Librarian recall.

Trust tier is derived from source_agent at query time — not stored on the
memory itself, so tier reassignments don't require rewriting memories.

Naming convention (defined here, pending adoption by all writers):
    cc/sprint            → tier_1 (CC during approved sprint, human in loop)
    igor/ne-checkpoint   → tier_2 (Igor during human-checkpointed NE cycle)
    igor/checkpoint      → tier_2
    <any other non-empty>→ tier_3 (autonomous, no human checkpoint)
    <empty or None>      → tier_0 (legacy / unknown origin)

Writers must prefix source_agent with these tokens for tier derivation to
be useful. Legacy memories (no source_agent) all land at tier_0.

Tier semantics:
    1 = highest trust (human explicitly in the loop)
    2 = high trust (human review in cycle)
    3 = low trust (autonomous, unreviewed)
    0 = unknown / legacy (no provenance at all)

Filter semantics for min_trust_tier:
    min_trust_tier=2 accepts tier_1 and tier_2, rejects tier_0 and tier_3.
    Formula: pass if 1 <= trust_tier <= min_trust_tier.
    Tier_0 always fails when a filter is active (unknown origin ≠ trusted).
"""

from __future__ import annotations

TIER_DESCRIPTIONS: dict[int, str] = {
    0: "legacy/unknown — no source_agent",
    1: "CC approved sprint — human in loop",
    2: "Igor NE checkpoint — human-reviewed cycle",
    3: "autonomous — no human checkpoint",
}

_TIER_1_PREFIXES = ("cc/sprint", "cc-sprint")
_TIER_2_PREFIXES = ("igor/ne-checkpoint", "igor/checkpoint", "igor-ne-checkpoint")


def derive_trust_tier(source_agent: str | None) -> int:
    """Return trust tier (0-3) for the given source_agent string.

    0 = unknown/legacy, 1 = highest trust, 3 = lowest known trust.
    """
    if not source_agent:
        return 0
    for prefix in _TIER_1_PREFIXES:
        if source_agent.startswith(prefix):
            return 1
    for prefix in _TIER_2_PREFIXES:
        if source_agent.startswith(prefix):
            return 2
    return 3


def passes_min_tier(trust_tier: int, min_trust_tier: int | None) -> bool:
    """Return True if trust_tier satisfies the min_trust_tier filter.

    None filter → always True (no filtering).
    Active filter → True only when 1 <= trust_tier <= min_trust_tier.
    Tier_0 (unknown) always fails an active filter.
    """
    if min_trust_tier is None:
        return True
    return 1 <= trust_tier <= min_trust_tier

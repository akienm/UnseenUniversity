"""
dimensions.py — the caller CONTRACT for the dimensional inference router.

The dimensions half of D-inference-router-stack-decomposition-2026-07-08. A caller
describes its request as DIMENSIONS and never names a model or provider:

  - ticket_tier      the WORK's tier (how hard the task is)
  - builder_tier     the requesting WORKER's capability tier
  - domain           WHAT KIND of task ('coding', 'prose', ...); '' = generalist
  - urgency          how slow a source may be ('interactive'|'normal'|'batch')
  - escalation_allowed  whether the within-domain escalation walk may bump difficulty
                        up on capability failure (default True; set False to pin a
                        resolve deterministic for tests/proofs)

ORTHOGONALITY (the redesign's core thesis — do not fuse independent axes):
ticket_tier drives the a-priori DIFFICULTY seed (what model capability the WORK needs).
builder_tier is a SEPARATE axis the resolver (T-inference-resolver-compose) consumes for
allowed-connection gating / cascade — it is deliberately NOT folded into model difficulty,
because a high-tier worker doing easy work must not be forced onto an expensive model.

TIERS mirror the canonical role hierarchy (apprentice->builder->creator->master->guru).
The vocabulary is defined LOCALLY here rather than imported from granny — inference must
not depend on granny (independent deployability); this is the router's own dimension
vocab that happens to align with the role hierarchy.

This module is ADDITIVE: route() and _DEFAULT_RULES are untouched. The resolver will
consume RouteRequest; nothing dispatches through it yet.
"""

from __future__ import annotations

from dataclasses import dataclass

from unseen_university.devices.inference.routing_buckets import (
    DIFFICULTY_BUCKETS,
    URGENCY_LEVELS,
)

# The tier vocabulary (mirrors the canonical role hierarchy; see module docstring).
ROLE_TIERS: tuple[str, ...] = ("apprentice", "builder", "creator", "master", "guru")

# A-priori map from a tier to the difficulty bucket its WORK demands. Five tiers collapse
# onto three difficulty buckets (classify < code < design). This is only the SEED — the
# escalation walk refines it upward at resolve time.
_TIER_DIFFICULTY: dict[str, str] = {
    "apprentice": "classify",
    "builder": "code",
    "creator": "code",
    "master": "design",
    "guru": "design",
}


def difficulty_seed(ticket_tier: str) -> str:
    """A-priori difficulty bucket for a ticket_tier (the resolution START point).

    Driven by ticket_tier alone (the WORK's demand), NOT by builder_tier — the two axes
    stay orthogonal. Unknown tier -> 'code' (the safe middle, matching
    routing_buckets.task_class_to_difficulty's default). The escalation walk may bump
    this up; it never seeds above the work's demand.
    """
    return _TIER_DIFFICULTY.get(ticket_tier, "code")


@dataclass(frozen=True)
class RouteRequest:
    """A caller's inference request expressed purely as dimensions — never a model/provider.

    There is deliberately NO model_id / source_name field: the contract makes it
    structurally impossible for a caller to force a model or provider (passing one is a
    TypeError). ticket_tier/builder_tier must be in ROLE_TIERS; urgency in URGENCY_LEVELS.
    """

    ticket_tier: str
    builder_tier: str
    domain: str = ""
    urgency: str = "normal"
    escalation_allowed: bool = True

    def __post_init__(self) -> None:
        if self.ticket_tier not in ROLE_TIERS:
            raise ValueError(
                f"ticket_tier {self.ticket_tier!r} not in {ROLE_TIERS}"
            )
        if self.builder_tier not in ROLE_TIERS:
            raise ValueError(
                f"builder_tier {self.builder_tier!r} not in {ROLE_TIERS}"
            )
        if self.urgency not in URGENCY_LEVELS:
            raise ValueError(f"urgency {self.urgency!r} not in {URGENCY_LEVELS}")

    @property
    def seed_difficulty(self) -> str:
        """The a-priori difficulty bucket this request seeds resolution at."""
        return difficulty_seed(self.ticket_tier)


# Ensure the seed map only names real difficulty buckets (guards a typo'd bucket).
assert set(_TIER_DIFFICULTY.values()) <= set(DIFFICULTY_BUCKETS)

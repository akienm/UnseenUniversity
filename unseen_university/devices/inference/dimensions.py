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

RouteRequest is the LIVE entry contract: the resolver (rules_engine.resolve) consumes it for
every routing decision, and live dispatch composes it via the coding domain's select(). The
pre-cutover monolith (route() and _DEFAULT_RULES) has been deleted.
"""

from __future__ import annotations

from dataclasses import dataclass

from unseen_university.devices.inference.routing_buckets import (
    DIFFICULTY_BUCKETS,
    URGENCY_LEVELS,
)

# The tier vocabulary (mirrors the canonical role hierarchy; see module docstring).
ROLE_TIERS: tuple[str, ...] = ("apprentice", "builder", "creator", "master", "guru")

#: The human terminal at the top of the role ladder — guru is Akien (human-only tickets;
#: project_role_hierarchy, cc_queue VALID_ROLES). No MODEL stands at this rung: it is where
#: the escalation ladder hands off to a person. So guru is NOT a difficulty bucket; the
#: resolver short-circuits it to a human-terminal decision (rules_engine.HUMAN_TERMINAL)
#: rather than selecting a model. Keeping it off the difficulty axis is what makes the ladder
#: rungs real — every model rung below traces to a MEASURED capability bucket, and the top is
#: a person, not a phantom model (T-inference-tier-ladder-real).
HUMAN_TERMINAL_TIER = "guru"


def is_human_terminal(ticket_tier: str) -> bool:
    """True iff this tier is the human terminal (guru) — resolves to a person, no model."""
    return ticket_tier == HUMAN_TERMINAL_TIER


# A-priori map from a tier to the difficulty bucket its WORK demands. The FOUR model rungs
# (apprentice < builder < creator < master) map INJECTIVELY onto the four MEASURED difficulty
# buckets (classify < code < design < frontier) — each rung seeds a strictly higher bucket
# than the one below, so an escalation policy has a real, strictly-more-capable stack to walk
# to at every step (was: builder==creator=='code', master==guru=='design' — a collapse that
# left creator dead and gave escalation nothing to walk; T-inference-tier-ladder-real). The
# ordering is legitimate ONLY because each bucket is grounded in a model's MEASURED
# difficulty_capable (never a declared tier label) — see models_registry capability_evidence.
# This is only the SEED; the escalation walk refines it upward at resolve time. guru is absent
# here on purpose (the human terminal — see HUMAN_TERMINAL_TIER).
_TIER_DIFFICULTY: dict[str, str] = {
    "apprentice": "classify",
    "builder": "code",
    "creator": "design",
    "master": "frontier",
}


def difficulty_seed(ticket_tier: str) -> str:
    """A-priori difficulty bucket for a ticket_tier (the resolution START point).

    Driven by ticket_tier alone (the WORK's demand), NOT by builder_tier — the two axes
    stay orthogonal. The human terminal (guru) has no difficulty bucket — it is not a model
    rung — so it seeds at the top MEASURED bucket ('frontier') as a defensive floor; in
    practice the resolver short-circuits guru to HUMAN_TERMINAL before any seed is used.
    Any other unknown tier -> 'code' (the safe middle, matching
    routing_buckets.task_class_to_difficulty's default). The escalation walk may bump this
    up; it never seeds above the work's demand.
    """
    if is_human_terminal(ticket_tier):
        return "frontier"
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


#: The caller's task_class vocabulary -> ticket_tier (role) vocabulary. Both collapse onto
#: the same three difficulty buckets, and this mapping preserves the a-priori difficulty seed
#: for every task_class (minion->classify, worker/analyst/batch->code, designer->design).
#: designer->master (NOT guru) so the guru-work-is-design policy floor is not spuriously
#: imposed. Unknown task_class -> builder (the code-difficulty default).
#:
#: This lives HERE, in the routing layer that owns the dimension vocabulary — not on a domain
#: object. A domain is a CONSUMER of routing; when the bridge lived on BaseDomain.select() the
#: proxy had to import a domain to route, which made device -> domains -> agentic_loop ->
#: device a cycle (T-inference-break-proxy-domain-cycle).
TASK_CLASS_TO_TIER: dict[str, str] = {
    "minion": "apprentice",
    "worker": "builder",
    "analyst": "builder",
    "batch": "builder",
    "creator": "creator",
    "designer": "master",
}


def route_request(
    *,
    task_class: str = "worker",
    domain: str = "",
    urgency: str | None = None,
    foreground: bool = False,
    escalation_allowed: bool = True,
    builder_tier: str = "builder",
) -> RouteRequest:
    """Build a RouteRequest from a caller's task_class/domain/urgency.

    The single construction point for live dispatch: the proxy calls this, and so does the
    decomposition invariant proof, so both exercise the same bridge. `foreground` is the
    latency-sensitive shorthand for urgency='interactive' (a TIME filter, not a cost lever).

    builder_tier is currently inert in live dispatch (no shipped policy reads it except the
    coding/guru predicates) and defaults to 'builder'; wiring real worker tiers is a follow-on.
    """
    return RouteRequest(
        ticket_tier=TASK_CLASS_TO_TIER.get(task_class, "builder"),
        builder_tier=builder_tier,
        domain=domain,
        urgency=urgency or ("interactive" if foreground else "normal"),
        escalation_allowed=escalation_allowed,
    )


# Ensure the seed map only names real difficulty buckets (guards a typo'd bucket).
assert set(_TIER_DIFFICULTY.values()) <= set(DIFFICULTY_BUCKETS)

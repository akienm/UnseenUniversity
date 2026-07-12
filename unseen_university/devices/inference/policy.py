"""
policy.py — the RULES stack for the dimensional inference router.

Stack 4 of 4 in D-inference-router-stack-decomposition-2026-07-08. A rule here is a
PolicyRule: a predicate over DIMENSIONS (from a RouteRequest) that contributes to a
CapabilityEnvelope — min difficulty, required domain, required features. A PolicyRule
NEVER names a specific model_id or source_name. That anti-literal property is the whole
point: it is what the monolith's (task_class, model_id, source_name) triples violated,
and it is enforced by a runnable test (the router decomposition invariant).

The resolver (rules_engine.resolve) calls build_envelope(req, policies) to turn a
request into the capability envelope that the models + connections stacks are then
filtered against. This module lives in its own file (not rules_engine.py) so the rules
stack is a first-class stack like providers/models/connections; keeping it separate is
what let the monolith's _DEFAULT_RULES triples be deleted cleanly at the cutover without
touching the policy stack.

This stack is LIVE: every resolve() call composes it. The pre-cutover monolith (route()
and _DEFAULT_RULES) has been deleted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from unseen_university.devices.inference.dimensions import RouteRequest
from unseen_university.devices.inference.routing_buckets import DIFFICULTY_BUCKETS

log = logging.getLogger(__name__)


def _difficulty_rank(bucket: str) -> int:
    """Rank of a difficulty bucket (unknown -> 0, the floor), for max-tightening."""
    try:
        return DIFFICULTY_BUCKETS.index(bucket)
    except ValueError:
        return 0


@dataclass(frozen=True)
class CapabilityEnvelope:
    """What a model must satisfy to serve a request — capability only, never a model id.

    min_difficulty: the hardest difficulty bucket the model must be capable of.
    required_domain: '' = generalist ok; else the model must serve this domain.
    required_features: capability flags the model must carry (e.g. 'tools').
    """

    min_difficulty: str
    required_domain: str = ""
    required_features: frozenset[str] = field(default_factory=frozenset)

    def tighten(
        self,
        *,
        min_difficulty: str | None = None,
        required_domain: str | None = None,
        add_features: tuple[str, ...] = (),
    ) -> "CapabilityEnvelope":
        """Return a NEW envelope at least as strict as self (monotone — never loosens)."""
        md = self.min_difficulty
        if min_difficulty and _difficulty_rank(min_difficulty) > _difficulty_rank(md):
            md = min_difficulty
        return CapabilityEnvelope(
            min_difficulty=md,
            required_domain=required_domain or self.required_domain,
            required_features=self.required_features | set(add_features),
        )


@dataclass(frozen=True)
class PolicyRule:
    """A dimension predicate -> capability-envelope contribution. NO model/provider literal.

    `when` is a dict of dimension predicates; a rule matches a RouteRequest when EVERY
    predicate present is satisfied. Supported keys: domain_in, ticket_tier_in,
    builder_tier_in, urgency_in (each maps to a list of allowed values). On match, the
    rule contributes: min_difficulty (raise the floor), required_features (add), and
    required_domain (set).
    """

    label: str
    when: dict = field(default_factory=dict)
    min_difficulty: str = ""
    required_features: tuple[str, ...] = ()
    required_domain: str = ""

    _DIM_KEYS: tuple[str, ...] = (
        "domain_in",
        "ticket_tier_in",
        "builder_tier_in",
        "urgency_in",
    )

    def matches(self, req: RouteRequest) -> bool:
        """True when every predicate present in `when` is satisfied by the request."""
        dim_value = {
            "domain_in": req.domain,
            "ticket_tier_in": req.ticket_tier,
            "builder_tier_in": req.builder_tier,
            "urgency_in": req.urgency,
        }
        for key, allowed in self.when.items():
            if key not in dim_value:
                # Unknown predicate key = never matches (fail-safe, not fail-open).
                return False
            if dim_value[key] not in allowed:
                return False
        return True


# Default policy set — expresses ROUTING POLICY over dimensions, with zero model/provider
# literals. Kept small; grows as policy is learned. The anti-literal property is tested.
_DEFAULT_POLICIES: list[PolicyRule] = [
    # Coding work needs tool-use capability regardless of tier.
    PolicyRule(
        label="coding-needs-tools",
        when={"domain_in": ["coding"]},
        required_features=("tools",),
        required_domain="coding",
    ),
    # (Removed 'guru-work-is-design': guru is the HUMAN terminal — Akien, not a model rung —
    # so it never selects a model; the resolver short-circuits it to HUMAN_TERMINAL before the
    # envelope is consulted. A policy floor pinning "guru work is design-difficulty" encoded
    # the old collapse where guru was a design-tier model rung; under the real ladder guru
    # sits ABOVE the top model bucket, so the floor was both stale and contradictory.
    # T-inference-tier-ladder-real.)
]


def build_envelope(
    req: RouteRequest, policies: list[PolicyRule] | None = None
) -> CapabilityEnvelope:
    """Compose a request's dimensions + matching policies into a CapabilityEnvelope.

    The base envelope comes from the request itself (seed_difficulty + domain); each
    matching PolicyRule then TIGHTENS it (monotone). No policy can loosen the envelope,
    and none names a model/provider — the envelope is pure capability, which the resolver
    filters the models + connections stacks against.
    """
    pols = _DEFAULT_POLICIES if policies is None else policies
    env = CapabilityEnvelope(
        min_difficulty=req.seed_difficulty, required_domain=req.domain
    )
    for p in pols:
        if p.matches(req):
            env = env.tighten(
                min_difficulty=p.min_difficulty or None,
                required_domain=p.required_domain or None,
                add_features=p.required_features,
            )
            log.debug("policy: %s matched -> envelope %s", p.label, env)
    return env

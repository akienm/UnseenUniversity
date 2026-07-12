"""Tests for the rules-as-policy stack (T-inference-rules-as-policy).

The rules stack (stack 4 of 4, D-inference-router-stack-decomposition-2026-07-08) expresses
policy over DIMENSIONS as capability envelopes, never naming a model or provider. These
tests pin: the base envelope seeds from the request, matching policies tighten (never
loosen), predicates require ALL keys, unknown keys fail safe, and — the load-bearing
invariant — no default policy contains a model_id or source_name literal.
"""

from __future__ import annotations

from unseen_university.devices.inference.connections import default_connections
from unseen_university.devices.inference.dimensions import RouteRequest
from unseen_university.devices.inference.models_registry import default_registry
from unseen_university.devices.inference.policy import (
    _DEFAULT_POLICIES,
    CapabilityEnvelope,
    PolicyRule,
    build_envelope,
)


def test_build_envelope_seeds_from_request_and_applies_policy():
    """A coding/builder request seeds min_difficulty=code and gains the coding-tools policy."""
    req = RouteRequest(ticket_tier="builder", builder_tier="builder", domain="coding")
    env = build_envelope(req)
    assert env.min_difficulty == "code"          # seed from ticket_tier=builder
    assert env.required_domain == "coding"        # from the request + coding policy
    assert "tools" in env.required_features        # coding-needs-tools policy contributed


def test_policy_tightens_never_loosens():
    """Tightening is monotone (max, never lower): a floor raised above the seed stays raised,
    and tightening with a LOWER difficulty cannot loosen it.

    (Uses a builder/coding request — seed 'code' — rather than guru: guru is now the human
    terminal, short-circuited before the envelope, so it is no longer a build_envelope example.
    T-inference-tier-ladder-real.)"""
    req = RouteRequest(ticket_tier="builder", builder_tier="builder", domain="coding")
    env = build_envelope(req)
    assert env.min_difficulty == "code"                     # seed from ticket_tier=builder
    tightened = env.tighten(min_difficulty="design")         # raise the floor above the seed
    assert tightened.min_difficulty == "design"
    # tighten with a LOWER difficulty must not loosen an already-strict envelope.
    loosened = tightened.tighten(min_difficulty="classify")
    assert loosened.min_difficulty == "design"


def test_policy_requires_all_predicate_keys():
    """A PolicyRule with two predicates matches only when BOTH are satisfied."""
    rule = PolicyRule(
        label="coding-guru",
        when={"domain_in": ["coding"], "ticket_tier_in": ["guru"]},
        min_difficulty="design",
    )
    assert rule.matches(RouteRequest("guru", "guru", domain="coding"))
    assert not rule.matches(RouteRequest("builder", "guru", domain="coding"))  # tier miss
    assert not rule.matches(RouteRequest("guru", "guru", domain="prose"))       # domain miss


def test_unknown_predicate_key_fails_safe():
    """An unrecognized `when` key never matches (fail-safe, not fail-open)."""
    rule = PolicyRule(label="bogus", when={"phase_of_moon_in": ["full"]})
    assert not rule.matches(RouteRequest("builder", "builder"))


def test_no_model_or_provider_literals_in_default_policies():
    """THE invariant: no default policy names a model_id or source_name."""
    models = default_registry()
    # Provider literals now live on the connections stack (ModelSpec.source_name is deleted);
    # the invariant is unchanged — no default policy may name a model or a provider.
    conns = default_connections(models)
    forbidden = {m.model_id for m in models.all()} | {c.source_name for c in conns.all()}
    for p in _DEFAULT_POLICIES:
        strings = {p.label, p.required_domain, *p.required_features}
        for allowed in p.when.values():
            strings |= set(allowed)
        leaked = strings & forbidden
        assert not leaked, f"policy {p.label} leaks model/provider literal(s): {leaked}"

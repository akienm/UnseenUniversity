"""Tests for the dimensional caller contract (T-inference-dimensions-contract).

The contract (D-inference-router-stack-decomposition-2026-07-08): callers describe a
request as dimensions {ticket_tier, builder_tier, domain, urgency, escalation_allowed}
and can NEVER name a model or provider. ticket_tier drives the a-priori difficulty seed;
builder_tier is a separate axis. These tests pin the field shape, the deterministic seed,
the default escalation flag, structural rejection of a model/provider, and validation.
"""

from __future__ import annotations

import pytest

from unseen_university.devices.inference.dimensions import (
    ROLE_TIERS,
    RouteRequest,
    difficulty_seed,
)


def test_route_request_accepts_dimensions_escalation_defaults_true():
    """A RouteRequest carries the five dimensions; escalation_allowed defaults True."""
    req = RouteRequest(ticket_tier="builder", builder_tier="master", domain="coding")
    assert req.ticket_tier == "builder"
    assert req.builder_tier == "master"
    assert req.domain == "coding"
    assert req.urgency == "normal"
    assert req.escalation_allowed is True


def test_escalation_allowed_can_be_pinned_false():
    """escalation_allowed=False is honored — the determinism lever for tests/proofs."""
    req = RouteRequest(ticket_tier="builder", builder_tier="builder", escalation_allowed=False)
    assert req.escalation_allowed is False


def test_difficulty_seed_is_deterministic_and_tier_driven():
    """difficulty_seed maps ticket_tier -> a difficulty bucket, driven by the WORK tier."""
    assert difficulty_seed("apprentice") == "classify"
    assert difficulty_seed("builder") == "code"
    assert difficulty_seed("creator") == "code"
    assert difficulty_seed("master") == "design"
    assert difficulty_seed("guru") == "design"
    # unknown tier -> safe middle
    assert difficulty_seed("nonesuch") == "code"


def test_seed_difficulty_is_orthogonal_to_builder_tier():
    """The seed follows ticket_tier only — builder_tier does NOT raise model difficulty."""
    easy_work_strong_worker = RouteRequest(ticket_tier="apprentice", builder_tier="guru")
    assert easy_work_strong_worker.seed_difficulty == "classify"


def test_caller_cannot_supply_a_model_or_provider():
    """The contract structurally forbids naming a model/provider (no such field)."""
    with pytest.raises(TypeError):
        RouteRequest(ticket_tier="builder", builder_tier="builder", model_id="deepseek-v4")
    with pytest.raises(TypeError):
        RouteRequest(ticket_tier="builder", builder_tier="builder", source_name="openrouter")


def test_invalid_tier_or_urgency_rejected():
    """Unknown tiers / urgency are rejected at construction."""
    with pytest.raises(ValueError):
        RouteRequest(ticket_tier="wizard", builder_tier="builder")
    with pytest.raises(ValueError):
        RouteRequest(ticket_tier="builder", builder_tier="wizard")
    with pytest.raises(ValueError):
        RouteRequest(ticket_tier="builder", builder_tier="builder", urgency="whenever")
    # sanity: every declared role tier constructs cleanly
    for t in ROLE_TIERS:
        RouteRequest(ticket_tier=t, builder_tier=t)

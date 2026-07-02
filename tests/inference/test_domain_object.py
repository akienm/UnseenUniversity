"""
Tests for T-domain-object-base (D-domain-object-encapsulation-2026-07-01):
the Domain object owns model selection + prompts for one KIND of task.

Behavior-preserving: CodingDomain.select() reproduces exactly what
RulesEngine.route(domain='coding') chooses today, and CodingDomain's prompt is
byte-identical to the coding domain-prompt. An unknown domain resolves to a
generalist BaseDomain (name passed through, empty prompt) — no crash, no name
collapse. Escalation / loop ownership is OUT of scope (next ticket).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.inference.domain_prompts import domain_prompt
from unseen_university.devices.inference.domains import (
    BaseDomain,
    CodingDomain,
    resolve_domain,
)
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.rules_engine import RoutingRule, RulesEngine
from unseen_university.devices.inference.sources import Source, SourceRegistry


def _src(name, cost_class, *, available=True, time_bucket="interactive"):
    s = MagicMock(spec=Source)
    s.name = name
    s.available = available
    s.cost_class = cost_class
    s.time_bucket = time_bucket
    s.billing_type = "usage_based"
    return s


def _engine():
    """A stub engine with a coding-tagged and a prose-tagged worker model.

    The two carry different domain tags so a domain filter that flows through is
    observable: only the coding model may serve domain='coding'.
    """
    reg = SourceRegistry()
    reg.register(_src("cloud_code", "token_direct"))
    reg.register(_src("cloud_prose", "token_direct"))
    models = ModelsRegistry([
        ModelSpec("code-m", "cloud_code", "worker", 0.05, 0.05, 8192, domains=["coding"]),
        ModelSpec("prose-m", "cloud_prose", "worker", 0.01, 0.01, 8192, domains=["prose"]),
    ])
    rules = [
        # prose-m is cheaper — it would win if the domain filter did NOT flow through.
        RoutingRule(1, "worker", "prose-m", "cloud_prose", "prose"),
        RoutingRule(2, "worker", "code-m", "cloud_code", "code"),
    ]
    return RulesEngine(reg, models, rules)


# ── select() reproduces route(domain=...) ─────────────────────────────────────


def test_coding_domain_select_matches_route_for_coding():
    """CodingDomain.select() picks exactly what route(domain='coding') picks today."""
    engine = _engine()
    via_domain = CodingDomain().select(engine, task_class="worker")
    via_route = _engine().route(task_class="worker", domain="coding")
    assert via_domain is not None
    assert via_domain.model.model_id == via_route.model.model_id == "code-m"
    assert via_domain.source.name == via_route.source.name == "cloud_code"


def test_select_domain_filter_flows_through():
    """The domain filter is real: coding excludes the cheaper prose-only model."""
    # A generalist request would take the cheaper prose-m; the coding domain must not.
    generalist = _engine().route(task_class="worker", domain="")
    assert generalist.model.model_id == "prose-m"  # cheapest wins with no domain filter
    coding = CodingDomain().select(_engine(), task_class="worker")
    assert coding.model.model_id == "code-m"  # domain filter excludes prose-m


# ── prompts ───────────────────────────────────────────────────────────────────


def test_coding_domain_prompt_matches_store():
    """CodingDomain.prompts.system is the coding domain-prompt, byte-identical."""
    assert CodingDomain().prompts.system == domain_prompt("coding")
    assert CodingDomain().prompts.system, "coding prompt must be non-empty"


# ── resolution: registered vs generalist/unknown ──────────────────────────────


def test_resolve_registered_domain_is_specialized_subclass():
    d = resolve_domain("coding")
    assert isinstance(d, CodingDomain)
    assert d.name == "coding"


def test_resolve_unknown_domain_is_generalist_base_no_crash():
    """An unknown name resolves to a BaseDomain carrying that name — empty prompt, no crash."""
    d = resolve_domain("no-such-domain")
    assert isinstance(d, BaseDomain) and not isinstance(d, CodingDomain)
    assert d.name == "no-such-domain"  # name passed through, NOT collapsed to ''
    assert d.prompts.system == ""  # no specialized prompt
    # select must not crash and must delegate with the passed-through name.
    result = d.select(_engine(), task_class="worker")
    expected = _engine().route(task_class="worker", domain="no-such-domain")
    # Both see domain='no-such-domain' → identical selection (behavior-preserving).
    assert (result is None) == (expected is None)
    if result is not None:
        assert result.model.model_id == expected.model.model_id


def test_resolve_empty_domain_is_generalist_base():
    d = resolve_domain("")
    assert isinstance(d, BaseDomain) and not isinstance(d, CodingDomain)
    assert d.name == ""
    assert d.prompts.system == ""

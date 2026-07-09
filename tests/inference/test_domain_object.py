"""
Tests for T-domain-object-base (D-domain-object-encapsulation-2026-07-01):
the Domain object owns model selection + prompts for one KIND of task.

CodingDomain.select() composes the dimensional resolver (rules_engine.resolve) for a
domain='coding' request and picks the coding-tagged model, and CodingDomain's prompt is
byte-identical to the coding domain-prompt. An unknown domain resolves to a
generalist BaseDomain (name passed through, empty prompt) — no crash, no name
collapse. Escalation / loop ownership is OUT of scope (next ticket).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.inference.connections import Connection, ConnectionsRegistry
from unseen_university.devices.inference.dimensions import RouteRequest
from unseen_university.devices.inference.domain_prompts import domain_prompt
from unseen_university.devices.inference.domains import (
    BaseDomain,
    CodingDomain,
    resolve_domain,
)
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.rules_engine import RulesEngine
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
        ModelSpec("code-m", "worker", 0.05, 0.05, 8192, domains=["coding"]),
        ModelSpec("prose-m", "worker", 0.01, 0.01, 8192, domains=["prose"]),
    ])
    # prose-m is cheaper (0.02 < 0.10) — it would win generalist if the domain filter did
    # NOT flow through. policies=[] isolates the domain filter from the coding-needs-tools
    # policy (these synthetic models carry no 'tools' feature).
    conns = ConnectionsRegistry()
    conns.register(Connection("code-m", "cloud_code", 0.10))
    conns.register(Connection("prose-m", "cloud_prose", 0.02))
    return RulesEngine(reg, models, connections=conns, policies=[])


# ── select() composes the dimensional resolver (domain filter) ────────────────


def test_coding_domain_select_picks_the_coding_model():
    """CodingDomain.select() resolves to the coding-tagged model via the resolver."""
    engine = _engine()
    via_domain = CodingDomain().select(engine, task_class="worker")
    assert via_domain is not None
    assert via_domain.model.model_id == "code-m"
    assert via_domain.source.name == "cloud_code"


def test_select_domain_filter_flows_through():
    """The domain filter is real: coding excludes the cheaper prose-only model."""
    # A generalist request would take the cheaper prose-m; the coding domain must not.
    generalist = _engine().resolve(
        RouteRequest(ticket_tier="builder", builder_tier="builder", domain="")
    )
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


def test_resolve_unknown_domain_is_generalist_base_no_crash(monkeypatch):
    """An unknown name resolves to a BaseDomain carrying that name — empty prompt, no crash."""
    import unseen_university.system_alarms as sa
    # select() passes escalation_allowed=True; an unknown domain has no eligible model,
    # so resolve() would fire the terminal system_alarm (a filesystem drop). Stub it — the
    # assertion here is about the domain object, not the alarm path.
    monkeypatch.setattr(sa, "raise_alarm", lambda **kw: None)

    d = resolve_domain("no-such-domain")
    assert isinstance(d, BaseDomain) and not isinstance(d, CodingDomain)
    assert d.name == "no-such-domain"  # name passed through, NOT collapsed to ''
    assert d.prompts.system == ""  # no specialized prompt
    # select must not crash and must delegate with the passed-through name. The synthetic
    # rack has only coding/prose models, so an unknown domain matches nothing → None.
    result = d.select(_engine(), task_class="worker")
    assert result is None


def test_resolve_empty_domain_is_generalist_base():
    d = resolve_domain("")
    assert isinstance(d, BaseDomain) and not isinstance(d, CodingDomain)
    assert d.name == ""
    assert d.prompts.system == ""

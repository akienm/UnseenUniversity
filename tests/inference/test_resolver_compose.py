"""
Tests for T-inference-resolver-compose (4/6 of D-inference-router-stack-decomposition):
RulesEngine.resolve(RouteRequest) composes the 4 stacks — dimensions -> policy
capability envelope -> candidate MODELS -> their CONNECTIONS filtered by provider
availability + urgency/time eligibility -> the cost-optimizing selector picks the
cheapest capable connection.

resolve() is ADDITIVE and lives beside route(); nothing here touches route() or
_DEFAULT_RULES. The escalation contract mirrors route()'s external driver
(D-inference-domain-routing-2026-07-01): resolve() does ONE selection per call at a
(possibly overridden) difficulty; the caller (DS._run_inference) owns the hop counter
and re-calls with a bumped required_difficulty. escalation_allowed=False pins the pick
to the seed for deterministic proofs; escalation_allowed=True + a no-capable-connection
outcome fires the terminal system_alarm.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.inference.dimensions import RouteRequest
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


def _coding_rack():
    """A minimal 2-connection coding rack: cheap owned-local code model + pricey
    token-direct design model. Connections are seeded 1:1 from the models."""
    sources = SourceRegistry()
    sources.register(_src("hex", "owned_local"))       # cheapest
    sources.register(_src("cloud", "token_direct"))    # dearest
    models = ModelsRegistry([
        ModelSpec(
            "code-local", "hex", "worker", 0.0, 0.0, 8192,
            difficulty_capable="code", features=["tools"], domains=["coding"],
        ),
        ModelSpec(
            "design-cloud", "cloud", "worker", 3.0, 15.0, 8192,
            difficulty_capable="design", features=["tools"], domains=["coding"],
        ),
    ])
    # policies=[] → envelope is seed+domain only (no hidden feature floor) so these
    # tests isolate the dimension->connection->selector path; a separate test covers
    # policy tightening.
    return RulesEngine(sources, models, rules=[], policies=[])


# ── The consolidated proof node (proof-on-close points here) ──────────────────


def test_resolver_compose_proof(monkeypatch):
    """The dimensional resolver composes the 4 stacks with NO hardcoded triple, the
    escalation override genuinely changes the pick, and no-capable-connection under
    escalation_allowed fires the terminal system_alarm."""

    # (1) resolve returns a concrete (provider, model) with NO rule triple involved.
    # The engine is built with rules=[] — route() would find nothing — yet resolve()
    # still resolves, because it composes the connections stack, not _DEFAULT_RULES.
    eng = _coding_rack()
    req = RouteRequest(
        ticket_tier="builder", builder_tier="builder", domain="coding", urgency="normal"
    )
    dec = eng.resolve(req)
    assert dec is not None, "resolve must return a decision from the connections stack"
    # Cheapest capable connection wins: hex (owned_local) beats cloud (token_direct).
    assert dec.source.name == "hex"
    assert dec.model.model_id == "code-local"
    # The engine holds NO RoutingRule triples (rules=[]) — resolve() still resolves,
    # proving it composes the connections stack rather than the hardcoded triple path.
    assert not eng._rules

    # (2) The escalation override actually WALKS: escalation_allowed=True honors a
    # required_difficulty override (picks the pricier design connection);
    # escalation_allowed=False pins to the seed (picks the cheaper code connection).
    # Same request shape, DIFFERENT (provider, model) — not merely alarm-vs-None.
    pinned = RouteRequest(
        ticket_tier="builder", builder_tier="builder", domain="coding",
        urgency="normal", escalation_allowed=False,
    )
    walked = RouteRequest(
        ticket_tier="builder", builder_tier="builder", domain="coding",
        urgency="normal", escalation_allowed=True,
    )
    dec_pinned = _coding_rack().resolve(pinned, required_difficulty="design")
    dec_walked = _coding_rack().resolve(walked, required_difficulty="design")
    assert dec_pinned is not None and dec_walked is not None
    assert dec_pinned.model.model_id == "code-local"   # override ignored — seed pin
    assert dec_walked.model.model_id == "design-cloud"  # override honored — walked up
    assert (dec_pinned.source.name, dec_pinned.model.model_id) != (
        dec_walked.source.name, dec_walked.model.model_id
    )

    # (3) No capable connection under escalation_allowed=True -> terminal system_alarm.
    # A code-only rack asked (via override) for design capability: nothing serves.
    fired = {}

    def _spy(**kwargs):
        fired.update(kwargs)
        return MagicMock()

    import unseen_university.system_alarms as sa
    monkeypatch.setattr(sa, "raise_alarm", _spy)

    sources = SourceRegistry()
    sources.register(_src("hex", "owned_local"))
    models = ModelsRegistry([
        ModelSpec("code-only", "hex", "worker", 0.0, 0.0, 8192,
                  difficulty_capable="code", domains=["coding"]),
    ])
    code_only = RulesEngine(sources, models, rules=[], policies=[])

    alarm_req = RouteRequest(
        ticket_tier="builder", builder_tier="builder", domain="coding",
        urgency="normal", escalation_allowed=True,
    )
    dec_alarm = code_only.resolve(alarm_req, required_difficulty="design")
    assert dec_alarm is None, "no capable connection must resolve to None"
    assert fired, "escalation_allowed=True + no capable connection must fire a system_alarm"
    assert fired.get("level") == "WARNING"

    # ...and escalation_allowed=False on the same rack is a silent deterministic None
    # (no alarm) when even the seed rung cannot be served.
    fired.clear()
    sources2 = SourceRegistry()
    sources2.register(_src("hex", "owned_local"))
    models2 = ModelsRegistry([
        ModelSpec("prose-only", "hex", "worker", 0.0, 0.0, 8192,
                  difficulty_capable="code", domains=["prose"]),
    ])
    det = RulesEngine(sources2, models2, rules=[], policies=[])
    det_req = RouteRequest(
        ticket_tier="builder", builder_tier="builder", domain="coding",
        urgency="normal", escalation_allowed=False,
    )
    assert det.resolve(det_req) is None
    assert not fired, "escalation_allowed=False must NOT fire a system_alarm"


# ── Granular tests for the module's own suite ─────────────────────────────────


def test_unavailable_connection_is_skipped():
    """A connection whose provider is unavailable is not a candidate; the next
    cheapest available capable connection wins (availability != escalation)."""
    sources = SourceRegistry()
    sources.register(_src("hex", "owned_local", available=False))  # down
    sources.register(_src("cloud", "token_direct", available=True))
    models = ModelsRegistry([
        ModelSpec("m-local", "hex", "worker", 0.0, 0.0, 8192,
                  difficulty_capable="code", domains=["coding"]),
        ModelSpec("m-cloud", "cloud", "worker", 1.0, 1.0, 8192,
                  difficulty_capable="code", domains=["coding"]),
    ])
    eng = RulesEngine(sources, models, rules=[], policies=[])
    req = RouteRequest(ticket_tier="builder", builder_tier="builder", domain="coding")
    dec = eng.resolve(req)
    assert dec is not None
    assert dec.source.name == "cloud"  # hex down -> cloud at same difficulty


def test_urgency_filters_slow_connection():
    """An interactive-urgency request excludes an overnight-only source even if it is
    cheaper — time eligibility, reusing routing_buckets.urgency_time_eligible."""
    sources = SourceRegistry()
    sources.register(_src("slow_cheap", "owned_local", time_bucket="overnight"))
    sources.register(_src("fast_dear", "token_direct", time_bucket="interactive"))
    models = ModelsRegistry([
        ModelSpec("m-slow", "slow_cheap", "worker", 0.0, 0.0, 8192,
                  difficulty_capable="code", domains=["coding"]),
        ModelSpec("m-fast", "fast_dear", "worker", 1.0, 1.0, 8192,
                  difficulty_capable="code", domains=["coding"]),
    ])
    eng = RulesEngine(sources, models, rules=[], policies=[])
    req = RouteRequest(
        ticket_tier="builder", builder_tier="builder", domain="coding",
        urgency="interactive",
    )
    dec = eng.resolve(req)
    assert dec is not None
    assert dec.source.name == "fast_dear"  # slow_cheap filtered by urgency


def test_default_policy_tightens_envelope():
    """With the default policy set, a coding request requires the 'tools' feature
    (coding-needs-tools), so a tool-less model is excluded even when cheaper."""
    sources = SourceRegistry()
    sources.register(_src("hex", "owned_local"))
    sources.register(_src("cloud", "token_direct"))
    models = ModelsRegistry([
        ModelSpec("no-tools", "hex", "worker", 0.0, 0.0, 8192,
                  difficulty_capable="code", domains=["coding"]),           # no features
        ModelSpec("has-tools", "cloud", "worker", 1.0, 1.0, 8192,
                  difficulty_capable="code", features=["tools"], domains=["coding"]),
    ])
    eng = RulesEngine(sources, models, rules=[])  # policies=None -> _DEFAULT_POLICIES
    req = RouteRequest(ticket_tier="builder", builder_tier="builder", domain="coding")
    dec = eng.resolve(req)
    assert dec is not None
    assert dec.model.model_id == "has-tools"  # tool-less excluded by policy envelope


def test_connections_are_lazily_seeded_from_models():
    """No explicit ConnectionsRegistry -> resolve seeds a 1:1 snapshot from the model
    source_name bindings, so it resolves out of the box before cutover."""
    sources = SourceRegistry()
    sources.register(_src("hex", "owned_local"))
    models = ModelsRegistry([
        ModelSpec("m", "hex", "worker", 0.0, 0.0, 8192,
                  difficulty_capable="code", domains=["coding"]),
    ])
    eng = RulesEngine(sources, models, rules=[], policies=[])  # no connections passed
    req = RouteRequest(ticket_tier="builder", builder_tier="builder", domain="coding")
    dec = eng.resolve(req)
    assert dec is not None
    assert dec.source.name == "hex" and dec.model.model_id == "m"

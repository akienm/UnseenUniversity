"""
The cross-cutting runnable invariant of D-inference-router-stack-decomposition-2026-07-08,
so the four stacks cannot silently re-collapse into a monolith.

  "Add a connection for an existing model to the connections stack, touch NO rule, and a
   resolve for that model's dimensions returns the new connection."

This is the proof the original rot would have failed. In the monolith, model<->provider
reachability was smeared across ModelSpec.source_name (a 1:1 binding) AND the hardcoded
(task_class, model_id, source_name) rule triples, so making an existing model reachable on a
NEW provider meant EDITING RULES. Registering a Connection changed nothing, because dispatch
(device -> domain.select -> route) never read the connections stack.

The test therefore drives the real DISPATCH construction (dimensions.route_request, the
factory device.py itself uses), not a hand-rolled RouteRequest:
that is the seam where the collapse would reappear. It builds a rack whose ONLY available
provider is one no rule ever named, so every model is initially unreachable; then it adds a
single Connection — no rule, no ModelSpec, no code change — and the same dispatch call must
now resolve to it.

Deliberately constructs no ModelSpec, so the invariant is about an EXISTING model gaining a
new provider edge. (It now reads the typed no-path contract — `is_path` /
OUTCOME_NO_AVAILABLE_PROVIDER from T-inference-typed-no-path-result — since resolve() returns
a typed RoutingDecision rather than None for an unreachable rack; the baseline assertion is
the same fact, expressed on the current contract.)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.inference.connections import Connection
from unseen_university.devices.inference.device import _default_models
from unseen_university.devices.inference.dimensions import route_request
from unseen_university.devices.inference.rules_engine import (
    OUTCOME_NO_AVAILABLE_PROVIDER,
    RulesEngine,
)
from unseen_university.devices.inference.sources import Source, SourceRegistry

#: An existing model in the default registry (code difficulty, coding domain). We never
#: construct it — the invariant is about an EXISTING model gaining a new provider edge.
EXISTING_MODEL = "devstral-small-2:24b"

#: A provider no rule triple ever named. Owned-local so it is the cheapest cost_class.
NEW_PROVIDER = "hex-annex"


def _new_provider() -> Source:
    s = MagicMock(spec=Source)
    s.name = NEW_PROVIDER
    s.available = True
    s.cost_class = "owned_local"
    s.time_bucket = "interactive"
    s.billing_type = "usage_based"
    return s


def test_new_connection_needs_zero_rule_edits():
    """A brand-new model<->provider edge becomes dispatchable by registering ONE Connection.

    Red on the monolith (dispatch consulted the rule triples, so a Connection was inert and
    select() returned None); green once dispatch composes the connections stack.
    """
    # A rack whose only available provider is one NO rule ever mentions. Every default model
    # is therefore unreachable to begin with — the clean baseline.
    sources = SourceRegistry()
    sources.register(_new_provider())
    models = _default_models()
    engine = RulesEngine(sources, models, policies=[])

    # Exactly the RouteRequest the proxy builds for a live coding dispatch
    # (dimensions.route_request is device.py's own construction point — no domain object is
    # involved in routing; see T-inference-break-proxy-domain-cycle).
    def _dispatch():
        return engine.resolve(route_request(task_class="worker", domain="coding"))

    # Baseline: nothing is reachable — no connection lands on the only live provider. The
    # models ARE capable, just unreachable → a typed NO_AVAILABLE_PROVIDER no-path (not None;
    # T-inference-typed-no-path-result). is_path is the property under test here.
    baseline = _dispatch()
    assert not baseline.is_path, (
        "baseline must be unreachable: no model has a connection on the only available provider"
    )
    assert baseline.kind == OUTCOME_NO_AVAILABLE_PROVIDER, (
        f"capable-but-unreachable must be NO_AVAILABLE_PROVIDER, got {baseline.kind}"
    )

    # THE INVARIANT: add ONE connection for an EXISTING model on the new provider.
    # No rule is written, no ModelSpec is touched, no code changes.
    engine._resolve_connections().register(
        Connection(EXISTING_MODEL, NEW_PROVIDER, 0.0)
    )

    decision = _dispatch()

    assert decision.is_path, (
        "adding a connection for an existing model must make it dispatchable with ZERO rule "
        "edits — a no-path here means dispatch is still reading a rule stack that names models"
    )
    assert decision.model.model_id == EXISTING_MODEL
    assert decision.source.name == NEW_PROVIDER


def test_rules_stack_names_no_model_or_provider():
    """The rules stack carries no model/provider literal — the anti-collapse guard.

    The monolith's tell was a rules list whose rows named a model_id and a source_name. If a
    future change reintroduces one, the stacks have re-collapsed regardless of what resolve()
    happens to return.
    """
    import unseen_university.devices.inference.rules_engine as rules_engine_mod

    assert not hasattr(rules_engine_mod, "_DEFAULT_RULES")
    assert not hasattr(rules_engine_mod, "RoutingRule")
    assert not hasattr(RulesEngine, "route")

    # The policy stack expresses capability envelopes over DIMENSIONS only.
    from unseen_university.devices.inference.policy import _DEFAULT_POLICIES

    known_models = {m.model_id for m in _default_models().all()}
    for rule in _DEFAULT_POLICIES:
        blob = repr(rule)
        assert not (known_models & set(blob.split())), (
            f"policy rule {rule.label!r} names a concrete model — rules must constrain "
            f"capability over dimensions, never name a model or provider"
        )

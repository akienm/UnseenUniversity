"""Proof for T-inference-typed-no-path-result (D-domains-general-with-device-owned-specializations).

THE DEFECT (CP3, verified in the 2026-07-03 DS.0 observe-run): resolve() collapsed two
DISTINCT no-path causes onto a single None —
  (a) a CAPABILITY ceiling: no MODEL meets the envelope (nothing capable exists), and
  (b) an AVAILABILITY outage: a capable model exists but its providers are down.
dispatch turned both into one untyped error response, and every agentic loop read that
response as AVAILABILITY. So a capability ceiling was laundered into availability: the walk
RETRIED a doomed rung up to its retry bound, then halted with a LYING 'no live source' when
the truth was that nothing capable existed. Three mouths alarmed for one event (resolve's own,
device's chokepoint, the walk's availability-exhausted).

THE FIX, proven here against a hermetic rack (no live inference):

  1. resolve() returns a TYPED RoutingDecision (never None): OUTCOME_NO_CAPABLE_MODEL vs
     OUTCOME_NO_AVAILABLE_PROVIDER — see test_resolve_* below.
  2. dispatch encodes the kind into finish_reason (no_capable_model / no_provider) and
     SUPPRESSES its chokepoint alarm for a caller that owns its own walk (escalation_driven),
     while keeping it for every non-walk consumer — see test_dispatch_*.
  3. THE LOAD-BEARING CHANGE: the walk reads the type. A NO_CAPABLE_MODEL failure now
     ESCALATES a rung (spends up); a NO_AVAILABLE_PROVIDER failure RETRIES the same rung
     (does not spend). Exactly one alarm fires per walk — see the two walk tests, the proof
     nodes.

The proof asserts BEHAVIOR (which rung the walk advances to, which alarm sounds), not the
new symbols themselves, so reverting the impl makes it authentically red — pre-fix the
no_capable_model response is read as availability and the walk retries hop 0 three times and
halts on 'availability-exhausted', instead of climbing hops 0→1→2 and halting on
'capability-ceiling'.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.connections import Connection, ConnectionsRegistry
from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.dimensions import RouteRequest
from unseen_university.devices.inference.domains.base import BaseDomain
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.rules_engine import RulesEngine
from unseen_university.devices.inference.shim import InferenceRequest
from unseen_university.devices.inference.sources import Source, SourceRegistry

# NB: the OUTCOME_* kinds and RoutingDecision.kind are symbols THIS ticket introduces, so they
# are imported LOCALLY inside the tests that need them (never at module scope). The proof nodes
# below — the two walk tests — assert on BEHAVIOR (which rung the walk advances to, which alarm
# fires) using only pre-existing symbols + finish_reason string literals, so this file still
# COLLECTS when proof_emitter reverts the impl: its red is a real AssertionError, not a
# collateral ImportError of a not-yet-existing symbol (reference_proof_emitter_gotchas).


def _src(name: str, available: bool) -> MagicMock:
    s = MagicMock(spec=Source)
    s.name = name
    s.available = available
    s.cost_class = "owned_local"
    s.time_bucket = "interactive"
    s.billing_type = "usage_based"
    return s


def _coding_model(model_id: str, difficulty: str) -> ModelSpec:
    return ModelSpec(model_id, "worker", 0.0, 0.0, 8192,
                     difficulty_capable=difficulty, features=["tools"], domains=["coding"])


def _rack(models: list[ModelSpec], *, source_up: bool) -> RulesEngine:
    """A hermetic coding rack: the given models, each wired to ONE owned-local provider that is
    up or down per `source_up`. policies=[] isolates the projection→selector path."""
    sources = SourceRegistry()
    sources.register(_src("hex", source_up))
    conns = ConnectionsRegistry()
    for m in models:
        conns.register(Connection(m.model_id, "hex", 0.0))
    return RulesEngine(sources, ModelsRegistry(models), connections=conns, policies=[])


def _coding_req(*, escalation_allowed: bool = False) -> RouteRequest:
    return RouteRequest(ticket_tier="builder", builder_tier="builder", domain="coding",
                        escalation_allowed=escalation_allowed)


# ── resolve() returns the two no-path kinds DISTINCTLY (test plan parts 1 & 2) ──


def test_resolve_capable_model_on_down_provider_is_no_available_provider():
    """A capable model whose ONLY provider is down → NO_AVAILABLE_PROVIDER (retry, don't spend)."""
    from unseen_university.devices.inference.rules_engine import OUTCOME_NO_AVAILABLE_PROVIDER
    eng = _rack([_coding_model("code-m", "code")], source_up=False)
    dec = eng.resolve(_coding_req())
    assert dec.kind == OUTCOME_NO_AVAILABLE_PROVIDER
    assert not dec.is_path and dec.model is None


def test_resolve_no_capable_model_is_no_capable_model():
    """No model meets the (escalated) capability floor → NO_CAPABLE_MODEL (escalate a rung)."""
    from unseen_university.devices.inference.rules_engine import OUTCOME_NO_CAPABLE_MODEL
    # Only a classify-capable model exists, but the request floor is 'frontier' (master tier);
    # nothing capable stands there, though the provider is UP — so this is capability, not
    # availability.
    eng = _rack([_coding_model("classify-m", "classify")], source_up=True)
    dec = eng.resolve(RouteRequest(ticket_tier="master", builder_tier="builder", domain="coding",
                                   escalation_allowed=False))
    assert dec.kind == OUTCOME_NO_CAPABLE_MODEL
    assert not dec.is_path and dec.model is None


def test_resolve_capable_and_up_is_a_path():
    """Control: a capable model on a live provider still resolves to a real PATH."""
    from unseen_university.devices.inference.rules_engine import OUTCOME_PATH
    eng = _rack([_coding_model("code-m", "code")], source_up=True)
    dec = eng.resolve(_coding_req())
    assert dec.kind == OUTCOME_PATH and dec.is_path
    assert dec.model.model_id == "code-m"


def test_resolve_never_alarms_on_a_no_path():
    """resolve() itself raises NO alarm — the no-path is silent data the walk owns (retires
    the triple-alarm's first mouth)."""
    with patch("unseen_university.system_alarms.raise_alarm") as alarm:
        _rack([_coding_model("code-m", "code")], source_up=False).resolve(
            _coding_req(escalation_allowed=True), required_difficulty="frontier")
    assert alarm.call_count == 0


def _no_path_decision(kind: str):
    """A typed no-path RoutingDecision (imported locally so the module still collects when the
    impl — and thus RoutingDecision.kind — is reverted for a proof run)."""
    from unseen_university.devices.inference.rules_engine import RoutingDecision
    return RoutingDecision(source=None, model=None, kind=kind, rule_label=f"test:{kind}")


# ── dispatch encodes the kind + gates the chokepoint alarm (fix step 2) ──


def _no_source_device() -> InferenceDevice:
    """A device whose legacy _mode source does not exist, so any no-path lands on the
    complete-inference-failure chokepoint (no network call)."""
    return InferenceDevice(mode="nonexistent-mode", sources=SourceRegistry(),
                           models=ModelsRegistry([_coding_model("code-m", "code")]))


def _dispatch_with_route_outcome(kind: str, *, escalation_driven: bool):
    """Dispatch a coding request whose routing returns the given typed no-path kind; return
    (response, alarm_mock). _route is patched to isolate dispatch's encoding + gate."""
    dev = _no_source_device()
    no_path = _no_path_decision(kind)
    with patch.object(InferenceDevice, "_route", return_value=no_path), \
         patch("unseen_university.system_alarms.raise_alarm") as alarm:
        resp = dev.dispatch(InferenceRequest(
            messages=[{"role": "user", "content": "hi"}],
            task_class="worker", domain="coding", escalation_driven=escalation_driven,
        ))
    return resp, alarm


def test_dispatch_stamps_typed_finish_reason():
    """dispatch encodes the routing kind into finish_reason so the loop need not re-guess."""
    from unseen_university.devices.inference.rules_engine import (
        OUTCOME_NO_AVAILABLE_PROVIDER,
        OUTCOME_NO_CAPABLE_MODEL,
    )
    resp_cap, _ = _dispatch_with_route_outcome(OUTCOME_NO_CAPABLE_MODEL, escalation_driven=False)
    assert resp_cap.finish_reason == "no_capable_model" and resp_cap.source_kind == "none"
    resp_avail, _ = _dispatch_with_route_outcome(OUTCOME_NO_AVAILABLE_PROVIDER, escalation_driven=False)
    assert resp_avail.finish_reason == "no_provider" and resp_avail.source_kind == "none"


def test_dispatch_chokepoint_alarm_is_gated_by_escalation_driven():
    """The chokepoint alarm fires for a non-walk consumer (its sole no-source signal) and is
    SUPPRESSED for a walk-driven caller (which owns the one alarm at its terminal) — retires
    the triple-alarm's device-chokepoint mouth."""
    from unseen_university.devices.inference.rules_engine import OUTCOME_NO_CAPABLE_MODEL
    _, alarm_nonwalk = _dispatch_with_route_outcome(OUTCOME_NO_CAPABLE_MODEL, escalation_driven=False)
    assert alarm_nonwalk.call_count == 1, "a non-walk consumer must keep its no-source alarm"

    _, alarm_walk = _dispatch_with_route_outcome(OUTCOME_NO_CAPABLE_MODEL, escalation_driven=True)
    assert alarm_walk.call_count == 0, "a walk-driven dispatch must suppress the chokepoint alarm"


# ── THE PROOF NODES: the walk READS the type (fix step 3, the load-bearing change) ──


def _resp(finish_reason: str) -> MagicMock:
    """A no-live-source dispatch response with the given typed finish_reason (source_kind
    'none', zero productive turns — a clean turn-0 no-source outcome)."""
    r = MagicMock()
    r.text = ""
    r.tool_calls = None
    r.finish_reason = finish_reason
    r.source_kind = "none"
    r.source_billing_type = "usage_based"
    r.input_tokens = 0
    r.output_tokens = 0
    r.cost_estimate = 0.0
    r.model = ""
    return r


def _run_walk(finish_reason: str, *, capture=None):
    """Run BaseDomain.run for a coding ticket with dispatch mocked to always return a no-source
    response carrying `finish_reason`; return (result, dispatched_hops, alarm_mock). If a
    `capture` list is given, every dispatched request is appended to it."""
    hops: list[int] = []

    def dispatch(req):
        hops.append(req.escalation_hop)
        if capture is not None:
            capture.append(req)
        return _resp(finish_reason)

    with patch.object(InferenceDevice, "__init__", return_value=None), \
         patch.object(InferenceDevice, "dispatch", MagicMock(side_effect=dispatch)), \
         patch("unseen_university.system_alarms.raise_alarm") as alarm:
        domain = BaseDomain(name="coding")  # task_class 'worker' → base difficulty 'code'
        result = domain.run({"id": "T-typed", "title": "t", "description": "d"})
    return result, hops, alarm


def _alarm_signatures(alarm) -> list[str]:
    return [c.kwargs.get("signature", "") for c in alarm.call_args_list]


def test_walk_escalates_a_rung_on_no_capable_model():
    """PROOF NODE. A NO_CAPABLE_MODEL no-source response is a CAPABILITY failure: the walk
    climbs difficulty rungs (escalation_hop 0→1→2), then halts once at the honest capability
    ceiling. Pre-fix this response was read as availability, so the walk RETRIED hop 0 (0,0,0)
    and halted on a LYING 'availability-exhausted' — the authentic red (the CP3 bug)."""
    result, hops, alarm = _run_walk("no_capable_model")

    assert result is None, "escalated past the top rung with nothing capable → halt (None)"
    # The walk SPENT UP: each dispatch was at a strictly higher escalation hop, not a same-rung
    # retry. This is the whole capability-vs-availability distinction.
    assert hops == [0, 1, 2], (
        f"a capability ceiling must ESCALATE the rung each hop (0→1→2), not retry the same "
        f"rung — got dispatched hops {hops} (all-zero = the pre-fix availability laundering)"
    )
    # Exactly ONE alarm per walk, and it names the TRUE cause (capability ceiling), not the
    # lying 'availability-exhausted'.
    sigs = _alarm_signatures(alarm)
    assert len(sigs) == 1, f"exactly one alarm must fire per walk, got {sigs}"
    assert "capability-ceiling" in sigs[0], f"halt must name the true cause, got {sigs[0]!r}"


def test_walk_retries_same_rung_on_no_available_provider():
    """GUARD. A NO_AVAILABLE_PROVIDER no-source response is an AVAILABILITY failure: the walk
    RETRIES the SAME rung (escalation_hop stays 0 — it does NOT spend up to a pricier tier),
    bounded, then halts once on the honest 'availability-exhausted'. Confirms the fix did not
    turn every no-source into a capability escalation."""
    result, hops, alarm = _run_walk("no_provider")

    assert result is None
    assert hops == [0, 0, 0], (
        f"an availability outage must RETRY the same rung (no spend), got hops {hops}"
    )
    sigs = _alarm_signatures(alarm)
    assert len(sigs) == 1, f"exactly one alarm must fire per walk, got {sigs}"
    assert "availability-exhausted" in sigs[0], f"halt must name availability, got {sigs[0]!r}"


def test_walk_marks_every_dispatch_escalation_driven():
    """The 'one alarm per walk' invariant depends on the walk telling dispatch it OWNS the
    no-path (so the chokepoint suppresses its alarm). The mocked walk tests can't see that
    wiring, so pin it directly: every request the loop dispatches carries escalation_driven=True.

    NOT a proof node — on impl-revert `escalation_driven` won't exist on InferenceRequest, which
    would surface as an AttributeError rather than the proof nodes' clean AssertionError red.
    """
    reqs: list = []
    _run_walk("no_capable_model", capture=reqs)
    assert reqs, "the walk must have dispatched at least once"
    assert all(getattr(r, "escalation_driven", False) is True for r in reqs), (
        "every walk dispatch must set escalation_driven=True so dispatch suppresses the "
        "chokepoint alarm — otherwise a real walk would double-alarm (chokepoint + terminal)"
    )

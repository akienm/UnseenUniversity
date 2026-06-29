"""T-inf-reroute-B: igor's call()/DAG purpose path routes through the Proxy.

The second gateway surface — call(purpose_id, prompt, ctx) — used to traverse a
hand-rolled routing DAG to raw-urllib handlers (_h_ollama / _h_or), bypassing the
Inference Proxy. igor is now a normal Proxy consumer here too: each purpose maps
to a task_class and call() dispatches through InferenceDevice. What is pinned:

  * call() dispatches through the Proxy, not the raw DAG handlers.
  * purpose -> task_class mapping (preparse/winnow/think→minion, ne→analyst,
    reading_extract→batch).
  * ne's response_format rides InferenceRequest.extra (sources payload.update it).
  * foreground is derived from the call context (user turn / research).
  * the handler_override benchmark path still traverses the DAG (reading_benchmark),
    until T-inf-reroute-C moves it onto the Proxy and deletes the DAG.
  * call()'s str return contract is preserved.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.igor.cognition.inference_gateway import (
    Edge,
    InferenceContext,
    InferenceGateway,
    Node,
    PurposeConstraints,
    build_default_gateway,
)
from unseen_university.devices.inference.shim import InferenceResponse


def _spy(text="ok", cost=0.0, source_kind="local"):
    spy = MagicMock()
    spy.dispatch.return_value = InferenceResponse(
        text=text, cost_estimate=cost, source_kind=source_kind
    )
    return spy


def _ctx(**kw):
    base = dict(
        cloud_active=False, local_available=True, balance_ok=True, is_background=False
    )
    base.update(kw)
    return InferenceContext(**base)


def test_call_routes_through_proxy_dispatch():
    """PROOF NODE. call() must dispatch through the Proxy, not the raw DAG.

    Built minimally with a sentinel handler reachable by an always-true edge, so
    the pre-implementation tree (which traversed the DAG) returns the sentinel and
    NEVER calls dispatch -> assert_called_once raises AssertionError (authentic red
    for proof_emitter), not a collateral routing error. Post-impl, call() bypasses
    the DAG entirely and dispatches.
    """
    gw = InferenceGateway()
    gw._inference = _spy(text="proxy text")
    gw.add_node(Node(id="winnow"))
    gw.add_node(Node(id="h_sentinel", handler=lambda prompt, c, **k: "SENTINEL"))
    gw.add_edge(Edge("winnow", "h_sentinel", lambda ctx: True, priority=1))
    gw.register_purpose("winnow", PurposeConstraints(step_name="winnow"))

    out = gw.call("winnow", "prompt", _ctx())

    gw._inference.dispatch.assert_called_once()
    assert out == "proxy text"


def test_call_maps_purpose_to_task_class():
    """Each purpose selects the right Proxy task_class on the request."""
    expected = {
        "preparse": "minion",
        "winnow": "minion",
        "think": "minion",
        "ne": "analyst",
        "reading_extract": "batch",
    }
    for purpose, task_class in expected.items():
        gw = build_default_gateway()
        gw._inference = _spy()
        gw.call(purpose, "p", _ctx())
        req = gw._inference.dispatch.call_args.args[0]
        assert req.task_class == task_class, f"{purpose} -> {req.task_class}"


def test_call_ne_forwards_response_format():
    """ne's json_object response_format must ride InferenceRequest.extra."""
    gw = build_default_gateway()
    gw._inference = _spy()
    gw.call("ne", "p", _ctx())
    req = gw._inference.dispatch.call_args.args[0]
    assert req.extra.get("response_format") == {"type": "json_object"}


def test_call_foreground_derived_from_context():
    """foreground flips on a user turn / research chain, off for background."""
    gw = build_default_gateway()
    gw._inference = _spy()
    gw.call("preparse", "p", _ctx(is_user_turn=True))
    assert gw._inference.dispatch.call_args.args[0].foreground is True

    gw = build_default_gateway()
    gw._inference = _spy()
    gw.call("preparse", "p", _ctx(is_background=True))
    assert gw._inference.dispatch.call_args.args[0].foreground is False


def test_call_constraints_map_onto_request():
    """PurposeConstraints (tokens / temp / timeout) reach the request."""
    gw = build_default_gateway()
    gw._inference = _spy()
    gw.call("ne", "p", _ctx())
    req = gw._inference.dispatch.call_args.args[0]
    assert req.max_tokens == 1024  # ne constraint
    assert req.temperature == 0.3
    assert req.timeout == 45


def test_handler_override_still_traverses_dag():
    """Benchmark path: handler_override forces a DAG handler, NOT the Proxy."""
    gw = build_default_gateway()
    gw._inference = _spy()
    used = {}

    def _dag_handler(prompt, c, **k):
        used["via"] = "dag"
        return "dag-text"

    gw._nodes["ollama_winnow"].handler = _dag_handler

    out = gw.call("winnow", "p", _ctx(), handler_override="ollama_winnow")

    assert out == "dag-text"
    assert used["via"] == "dag"
    gw._inference.dispatch.assert_not_called()

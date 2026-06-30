"""T-inf-reroute-B/C: igor's call() purpose path routes through the Proxy.

call(purpose_id, prompt, ctx) builds an InferenceRequest (purpose -> task_class)
and dispatches through InferenceDevice — there is no routing DAG (T-inf-reroute-C
deleted it). What is pinned here:

  * call() dispatches through the Proxy (not raw handlers).
  * purpose -> task_class mapping (preparse/winnow/think->minion, ne->analyst,
    reading_extract->batch).
  * ne's response_format rides InferenceRequest.extra.
  * PurposeConstraints (tokens/temp/timeout) map onto the request.
  * foreground is derived from the call context (user turn / research).
  * an explicit model= (experiment/benchmark exception) is forwarded as req.model.
  * call()'s str return contract is preserved.

The authentic red→green proof for the reroute lives in test_no_direct_provider_*
(the grep gate); these are behavioral coverage on the new signatures.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.igor.cognition.inference_gateway import (
    InferenceContext,
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
    gw = build_default_gateway()
    gw._inference = _spy(text="proxy text")

    out = gw.call("winnow", "prompt", _ctx())

    gw._inference.dispatch.assert_called_once()
    assert out == "proxy text"


def test_call_maps_purpose_to_task_class():
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
    gw = build_default_gateway()
    gw._inference = _spy()
    gw.call("ne", "p", _ctx())
    req = gw._inference.dispatch.call_args.args[0]
    assert req.extra.get("response_format") == {"type": "json_object"}


def test_call_foreground_derived_from_context():
    gw = build_default_gateway()
    gw._inference = _spy()
    gw.call("preparse", "p", _ctx(is_user_turn=True))
    assert gw._inference.dispatch.call_args.args[0].foreground is True

    gw = build_default_gateway()
    gw._inference = _spy()
    gw.call("preparse", "p", _ctx(is_background=True))
    assert gw._inference.dispatch.call_args.args[0].foreground is False


def test_call_constraints_map_onto_request():
    gw = build_default_gateway()
    gw._inference = _spy()
    gw.call("ne", "p", _ctx())
    req = gw._inference.dispatch.call_args.args[0]
    assert req.max_tokens == 1024  # ne constraint
    assert req.temperature == 0.3
    assert req.timeout == 45


def test_call_forwards_explicit_model_through_proxy():
    """The experiment/benchmark exception: model= rides req.model (via the Proxy)."""
    gw = build_default_gateway()
    gw._inference = _spy()
    gw.call("reading_extract", "p", _ctx(), model="qwen/qwen-2.5-7b-instruct")
    assert gw._inference.dispatch.call_args.args[0].model == "qwen/qwen-2.5-7b-instruct"


def test_call_no_model_requests_tier_only():
    """Default path requests no specific model — pure tier routing."""
    gw = build_default_gateway()
    gw._inference = _spy()
    gw.call("winnow", "p", _ctx())
    assert gw._inference.dispatch.call_args.args[0].model == ""

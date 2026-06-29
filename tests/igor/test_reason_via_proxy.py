"""T-inf-reroute-A: igor's reason() routes through the canonical Inference Proxy.

igor is now a normal Proxy consumer — reason() assembles messages and calls
InferenceDevice.dispatch() (reader/summarizer/evaluator pattern), instead of
running its own tier ladder of direct Ollama/OpenRouter reasoners. The old
per-tier mechanics (and their tests) are deleted; what is pinned here is the
new contract:

  * reason() calls dispatch(), never a direct reasoner (.​_t4.reason / _t2.reason).
  * the (text, cost, used_api) return contract is preserved, with used_api
    reconstructed from response.source_kind ("cloud" -> True; else False) —
    the signal T-proxy-source-kind shipped for exactly this.
  * the public signature stays frozen across the ~8 callers.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.igor.cognition.inference_gateway import InferenceGateway
from unseen_university.devices.inference.shim import InferenceResponse


def _spy(text="hi", cost=0.0, source_kind="cloud"):
    spy = MagicMock()
    spy.dispatch.return_value = InferenceResponse(
        text=text, cost_estimate=cost, source_kind=source_kind
    )
    return spy


def test_reason_routes_through_proxy_dispatch():
    """PROOF NODE. reason() must call the Proxy's dispatch(), not the old ladder.

    Constructed bare (no _inference kwarg) and the spy attached by attribute, so
    the pre-implementation tree (which had no _get_inference and ran the tier
    ladder) reaches assert_called_once with dispatch NEVER called -> AssertionError
    (authentic red for proof_emitter), not a collateral construction error.
    """
    spy = _spy(text="proxy says hi", cost=0.012, source_kind="cloud")
    gw = InferenceGateway()
    gw._inference = spy

    text, cost, used_api = gw.reason("question", [], [], level="interactive")

    spy.dispatch.assert_called_once()
    assert text == "proxy says hi"
    assert cost == 0.012
    assert used_api is True


def test_used_api_true_only_for_cloud_source():
    gw = InferenceGateway(inference=_spy(source_kind="cloud"))
    _, _, used_api = gw.reason("q", [], [])
    assert used_api is True


def test_used_api_false_for_local_source():
    gw = InferenceGateway(inference=_spy(source_kind="local"))
    _, _, used_api = gw.reason("q", [], [])
    assert used_api is False


def test_used_api_false_for_no_source():
    gw = InferenceGateway(inference=_spy(source_kind="none"))
    _, _, used_api = gw.reason("q", [], [])
    assert used_api is False


def test_request_carries_assembled_messages_and_system():
    spy = _spy()
    gw = InferenceGateway(inference=spy)
    gw.reason("hello world", [], [], preparse_csb="PREP", level="interactive")
    req = spy.dispatch.call_args.args[0]
    assert req.messages and req.messages[0]["role"] == "user"
    # preparse_csb and the user input are folded into the single user message.
    assert "hello world" in req.messages[0]["content"]
    assert "PREP" in req.messages[0]["content"]
    assert isinstance(req.system, str)


def test_user_turn_sets_foreground_for_cloud_preference():
    """is_user_turn flips foreground=True so rules_engine prefers usage_based
    (cloud) — the surviving intent of the old D254 'human turns go cloud' rule.
    The actual source choice is the Proxy's, not igor's."""
    spy = _spy()
    gw = InferenceGateway(inference=spy)
    gw.reason("q", [], [], level="interactive", is_user_turn=True)
    req = spy.dispatch.call_args.args[0]
    assert req.foreground is True
    assert req.task_class == "analyst"


def test_local_only_declines_foreground():
    """local_only is honoured best-effort by declining foreground (prefer
    flat_rate / local). A hard force-local is deferred to igor's specifics."""
    spy = _spy(source_kind="local")
    gw = InferenceGateway(inference=spy)
    gw.reason("q", [], [], is_user_turn=True, local_only=True)
    req = spy.dispatch.call_args.args[0]
    assert req.foreground is False


def test_background_levels_map_to_cheap_task_classes():
    spy = _spy(source_kind="local")
    gw = InferenceGateway(inference=spy)
    gw.reason("q", [], [], level="background")
    assert spy.dispatch.call_args.args[0].task_class == "minion"
    spy.dispatch.reset_mock()
    gw.reason("q", [], [], level="background_batch")
    assert spy.dispatch.call_args.args[0].task_class == "batch"


def test_signature_frozen_accepts_all_caller_kwargs():
    """The ~8 reason() call sites pass these kwargs (incl. level=int from
    shadow_reasoner and skip_to from main.py:3426). reason() must still accept
    every one of them."""
    spy = _spy(text="ok", source_kind="cloud")
    gw = InferenceGateway(inference=spy)
    text, cost, used = gw.reason(
        "q",
        [],
        [],
        level=3,  # shadow_reasoner passes an int
        skip_to="tier.3.5",  # main.py:3426 still passes this
        preparse_csb="x",
        thread_id="t",
        cortex=None,
        instance_id="i",
        local_only=False,
        on_tier=lambda s: None,
        is_user_turn=True,
        complexity="high",
        prompt_role="analysis",
    )
    assert text == "ok"
    assert used is True

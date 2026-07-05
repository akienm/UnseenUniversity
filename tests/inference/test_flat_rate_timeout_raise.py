"""
Flat-rate inference-timeout raise proof (T-ds-hex-dispatch-timeout-midloop).

D-coding-loop-redesign-aider-survey-2026-07-04. The 2026-07-04 post-redesign confirm-by
showed the architect on devstral@Hex time out at 121s on turn 12: the loop hardcoded a
120s per-request wall regardless of source. A flat-rate/local box (Hex, $0) doesn't pay
per second, so the wall should be raised for it exactly as the turn cap already is
(MAX_TURNS → MAX_TURNS_FLAT_RATE at the turn-0 billing lock). This proves the raise:
after a flat_rate first response, the NEXT request carries a timeout well above the 120s
usage-based cliff.

THE PROOF: drive AgenticLoop with a fake device that records req.timeout per turn. Turn 0
returns a flat_rate response carrying a Read tool call (which forces a second dispatch);
turn 1 returns a done envelope to terminate. GREEN (raise active at HEAD): the turn-1
request's timeout is raised far above the base 120 (== 600). RED (raise reverted → the
120s hardcode): the turn-1 timeout is still 120, equal to turn 0 → the `> base` assertion
fails with an authentic AssertionError.

Revert-safety (proof_emitter_gotchas): imports ONLY stable symbols (AgenticLoop,
NativeToolCodec) — NOT the impl-added constant INFERENCE_TIMEOUT_FLAT_RATE, which is absent
at the reverted parent and would turn the red into an ImportError at collection. The claim
is asserted as `turn-1 timeout > turn-0 timeout` (behavioral), so the reverted file — which
still defines AgenticLoop + NativeToolCodec — imports cleanly and reaches the assertion.
"""

from __future__ import annotations

import json

from unseen_university.devices.inference.agentic_loop import AgenticLoop, NativeToolCodec


class _Resp:
    """Minimal InferenceResponse stand-in — only the fields the loop reads."""

    def __init__(self, *, text="", tool_calls=None, billing="usage_based", kind="owned_local"):
        self.text = text
        self.tool_calls = tool_calls
        self.finish_reason = "stop"
        self.source_kind = kind
        self.source_billing_type = billing
        self.input_tokens = 10
        self.output_tokens = 10
        self.cost_estimate = 0.0
        self.model = "devstral-mock"


class _RecordingDevice:
    """Fake InferenceDevice: records each request's timeout, drives a two-turn loop.

    Turn 0 → a flat_rate response with a Read tool call (forces a second dispatch so the
    turn-0 billing lock is exercised before we observe the next request's timeout).
    Turn 1 → a done envelope, terminating the loop.
    """

    def __init__(self):
        self.timeouts: list[int] = []

    def dispatch(self, req):
        self.timeouts.append(req.timeout)
        if len(self.timeouts) == 1:
            return _Resp(
                billing="flat_rate",
                tool_calls=[{
                    "id": "call_read_1", "type": "function",
                    "function": {"name": "Read", "arguments": json.dumps({"path": "README.md"})},
                }],
            )
        return _Resp(text=json.dumps({"status": "done", "result": "ok", "error_class": None}))


# ── THE PROOF NODE ────────────────────────────────────────────────────────────


def test_flat_rate_source_raises_the_per_request_timeout():
    """A flat_rate first response raises the subsequent request timeout above the 120s cliff.

    GREEN: turn-1 request timeout is raised at the turn-0 flat_rate lock, so it is strictly
    greater than the base turn-0 timeout (and lands at the flat-rate value, 600). RED (raise
    reverted): every request keeps the hardcoded 120s, so turn-1 == turn-0 == 120 and the
    `>` assertion fails — an authentic AssertionError, not an import/name error.
    """
    device = _RecordingDevice()
    AgenticLoop(codec=NativeToolCodec(), inference_device=device).run(
        system_prompt="",
        initial_message="do the task",
        ticket_id="T-timeout-proof",
    )

    assert len(device.timeouts) >= 2, (
        f"proof needs at least two dispatches to observe the raise; saw {device.timeouts!r}"
    )
    assert device.timeouts[0] == 120, (
        f"turn-0 request should use the base usage-based wall (120s); got {device.timeouts[0]}"
    )
    assert device.timeouts[1] > device.timeouts[0], (
        "a flat-rate source must raise the per-request timeout above the 120s usage-based "
        f"cliff after the turn-0 billing lock; turn-1 timeout stayed {device.timeouts[1]}s "
        f"(== turn-0 {device.timeouts[0]}s) — the hardcoded 120 was never raised"
    )
    assert device.timeouts[1] == 600, (
        f"flat-rate wall should be the flat-rate constant (600s); got {device.timeouts[1]}"
    )


class _LocalRecordingDevice:
    """Like _RecordingDevice, but turn 0 returns the REAL on-box Hex shape:
    source_kind='local' with billing_type='usage_based' (cost_class='owned_local').

    This is what OllamaSource actually reports — it is NOT flat_rate. The 2026-07-04 funnel
    caught every architect timing out at 120s on its plan turn precisely because the raise
    keyed on flat_rate alone and this source never matched.
    """

    def __init__(self):
        self.timeouts: list[int] = []

    def dispatch(self, req):
        self.timeouts.append(req.timeout)
        if len(self.timeouts) == 1:
            return _Resp(
                kind="local",
                billing="usage_based",
                tool_calls=[{
                    "id": "call_read_1", "type": "function",
                    "function": {"name": "Read", "arguments": json.dumps({"path": "README.md"})},
                }],
            )
        return _Resp(text=json.dumps({"status": "done", "result": "ok", "error_class": None}))


def test_local_source_raises_the_per_request_timeout():
    """An on-box LOCAL source (Hex: source_kind='local', billing='usage_based') raises the wall.

    A local box is $0 per second exactly like a flat-rate subscription, so it must get the
    same longer wall. GREEN (raise covers source_kind=='local'): turn-1 timeout is 600.
    RED (raise keyed on flat_rate ONLY): local+usage_based never matches, the timeout stays
    the 120s cliff, and both the `> turn-0` and `== 600` assertions fail — an authentic
    AssertionError. This is the production shape my flat-rate-only proof missed.
    """
    device = _LocalRecordingDevice()
    AgenticLoop(codec=NativeToolCodec(), inference_device=device).run(
        system_prompt="",
        initial_message="do the task",
        ticket_id="T-local-timeout-proof",
    )

    assert len(device.timeouts) >= 2, (
        f"proof needs at least two dispatches to observe the raise; saw {device.timeouts!r}"
    )
    assert device.timeouts[0] == 120, (
        f"turn-0 request should use the base wall (120s); got {device.timeouts[0]}"
    )
    assert device.timeouts[1] > device.timeouts[0], (
        "an on-box local source must raise the per-request timeout above the 120s cliff after "
        f"the turn-0 lock; turn-1 timeout stayed {device.timeouts[1]}s (== turn-0 "
        f"{device.timeouts[0]}s) — a local source was treated as a usage-based paid one"
    )
    assert device.timeouts[1] == 600, (
        f"local free-wall should reach the flat-rate constant (600s); got {device.timeouts[1]}"
    )

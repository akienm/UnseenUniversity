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

    def __init__(self, *, text="", tool_calls=None, billing="usage_based"):
        self.text = text
        self.tool_calls = tool_calls
        self.finish_reason = "stop"
        self.source_kind = "owned_local"
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

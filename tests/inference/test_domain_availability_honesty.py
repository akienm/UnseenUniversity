"""
Escalation-honesty tests for the domain walk (T-ds-loop-no-identical-retry-honest-escalate).

The domain `run()` walk classifies a LoopResult and, on an availability failure, retries.
The 2026-07-03 DS.0 observe-run showed the pathology (confirmed live in the re-run log):
a long loop did productive work at hop 0, hit a MID-RUN availability wall (a 120s dispatch
timeout), and the walk discarded the whole run and re-ran the ENTIRE loop from turn 0 — the
identical doomed path — up to `max_availability_retries + 1` times before halting on a
LYING `availability-exhausted` alarm (the true cause was a mid-run wall, not an exhausted
source pool).

The fix cuts on `LoopResult.turns`:
  - turns > 0 (productive work, then walled) → halt honestly, no blind full-loop re-run.
  - turns == 0 (source never came up) → a genuine transient blip; the cheap bounded retry
    is KEPT by design.

The InferenceDevice is mocked so the walk runs with no live source. We count dispatches:
one full loop run = 2 dispatches here (a turn-0 tool call, then a no-source turn), so the
doomed-retry path issues 6 and the honest path issues 2.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.domains.base import BaseDomain


def _tool_call_response() -> MagicMock:
    """A turn-0 response with one Read tool call — the loop executes it and advances a turn."""
    r = MagicMock()
    r.text = ""
    r.tool_calls = [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "Read", "arguments": json.dumps({"path": "/nonexistent/x"})},
    }]
    r.finish_reason = "stop"
    r.source_kind = "cloud"
    r.source_billing_type = "usage_based"
    r.input_tokens = 10
    r.output_tokens = 10
    r.cost_estimate = 0.0
    r.model = "devstral-small-2:24b"
    return r


def _no_source_response() -> MagicMock:
    """A response the loop reads as 'no live source' → LOOP_AVAILABILITY."""
    r = MagicMock()
    r.text = ""
    r.tool_calls = None
    r.finish_reason = "error"
    r.source_kind = "none"
    r.source_billing_type = "usage_based"
    r.input_tokens = 0
    r.output_tokens = 0
    r.cost_estimate = 0.0
    r.model = ""
    return r


def _run_walk(dispatch_side_effect):
    """Run BaseDomain.run with dispatch mocked; return (result, dispatch_mock, alarm_mock)."""
    dispatch = MagicMock(side_effect=dispatch_side_effect)
    with patch.object(InferenceDevice, "__init__", return_value=None), \
         patch.object(InferenceDevice, "dispatch", dispatch), \
         patch("unseen_university.system_alarms.raise_alarm") as alarm:
        domain = BaseDomain(name="coding")  # task_class 'worker' → base difficulty 'code'
        result = domain.run({"id": "T-test", "title": "t", "description": "d"})
    return result, dispatch, alarm


# ── THE PROOF NODE: a mid-run availability wall halts honestly, no doomed re-run ──


def test_midrun_availability_wall_halts_without_full_loop_reretry():
    """turns>0 availability → ONE loop run then an honest halt — not N identical re-runs.

    Proof node: green issues exactly 2 dispatches (turn-0 tool call + no-source turn → halt).
    Reverting the impl restores the blind retry, which re-runs the whole loop
    max_availability_retries+1 = 3 times = 6 dispatches → authentic AssertionError.
    """
    seq = {"n": 0}

    def dispatch(req):
        seq["n"] += 1
        # Odd calls = a productive turn-0 tool call; even calls = the mid-run no-source wall.
        return _tool_call_response() if seq["n"] % 2 == 1 else _no_source_response()

    result, dispatch_mock, alarm = _run_walk(dispatch)

    assert dispatch_mock.call_count == 2, (
        f"a mid-run availability wall must halt after ONE loop run (2 dispatches), not "
        f"re-run the identical full loop — got {dispatch_mock.call_count} dispatches"
    )
    assert result is None, "an honest mid-run-wall halt returns None"
    # And the halt names the TRUE cause, not the lying 'availability-exhausted'.
    sig = alarm.call_args.kwargs.get("signature", "") if alarm.call_args else ""
    assert "availability-midrun-wall" in sig, f"halt must name the true cause, got {sig!r}"


# ── GUARD: turns==0 (source never came up) keeps the cheap bounded transient retry ──


def test_turn0_source_down_still_bounded_retries():
    """turns==0 is a genuine start-up blip — the bounded retry path is intentionally kept.

    Every dispatch is no-source, so each loop run fails at turn 0 (turns==0). The walk
    retries max_availability_retries times then halts on the honest 'availability-exhausted'
    — 3 dispatches total (1 initial + 2 retries). Guards that the fix touched ONLY the
    productive-then-walled case, not this one.
    """
    result, dispatch_mock, alarm = _run_walk(lambda req: _no_source_response())

    assert dispatch_mock.call_count == 3, (
        f"turns==0 keeps the bounded transient retry (1 + {2} retries = 3 dispatches), "
        f"got {dispatch_mock.call_count}"
    )
    assert result is None
    sig = alarm.call_args.kwargs.get("signature", "") if alarm.call_args else ""
    assert "availability-exhausted" in sig, (
        f"a true source-down exhaust keeps the honest availability-exhausted halt, got {sig!r}"
    )

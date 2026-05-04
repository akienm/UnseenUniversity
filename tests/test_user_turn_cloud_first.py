"""
tests/test_user_turn_cloud_first.py

Locks in D254 (user turns route cloud-first, local is fallback only).

Discovered during T-user-turn-cloud-first-routing: the existing routing in
inference_gateway.py:651 already implements D254 correctly via the
`not is_user_turn` guard on local-first. These tests assert the contract
so a future refactor can't silently invert it again.

The ACTUAL bug from the 2026-05-03 20:17:27 incident (where Akien's
message went to local) is NOT a routing-logic bug — it's that
is_user_turn was False when the message reached gateway.reason(). That
upstream flag-coverage issue is filed as T-is-user-turn-flag-coverage.
"""

from unittest.mock import MagicMock


def _make_gateway():
    from wild_igor.igor.cognition.inference_gateway import InferenceGateway

    gw = InferenceGateway.__new__(InferenceGateway)
    gw._t2 = MagicMock(name="t2_ollama")
    gw._t2_batch = None
    gw._t3 = None
    gw._t35 = MagicMock(name="t35_haiku")
    gw._t4 = MagicMock(name="t4_sonnet")
    gw._t5 = None
    gw.last_tier = ""
    return gw


def _make_cortex():
    cortex = MagicMock()
    cortex.twm_read.return_value = []
    return cortex


# ── User-turn must go cloud-first (D254) ─────────────────────────────────────


def test_user_turn_routes_cloud_first_local_never_attempted_for_first():
    """is_user_turn=True → cloud is the FIRST tier attempted; local is never
    even tried for the first attempt. D254 contract."""
    gw = _make_gateway()
    gw._t4.reason.return_value = ("cloud reply", 0.001)
    cortex = _make_cortex()

    text, cost, used_api = gw.reason(
        "akien speaks",
        [],
        [],
        level="interactive",
        cortex=cortex,
        is_user_turn=True,
        complexity="low",
    )

    # Cloud was called.
    gw._t4.reason.assert_called_once()
    # Local-first was NEVER called (would happen at line ~651 if is_user_turn=False).
    gw._t2.reason.assert_not_called()
    assert used_api is True
    assert gw.last_tier == "cloud/interactive"


def test_user_turn_falls_back_to_local_only_after_cloud_fails():
    """is_user_turn=True + cloud fails → local retry IS allowed (per D254
    'human turns always allowed to retry Ollama as budget-exhaustion
    fallback'). But local-first NEVER fires; only the post-cloud retry."""
    gw = _make_gateway()
    gw._t4.reason.side_effect = RuntimeError("openrouter went down")
    gw._t2.reason.return_value = ("local fallback", 0.0)
    cortex = _make_cortex()

    text, cost, used_api = gw.reason(
        "akien speaks",
        [],
        [],
        level="interactive",
        cortex=cortex,
        is_user_turn=True,
        complexity="low",
    )

    # Cloud was attempted first (and failed).
    gw._t4.reason.assert_called_once()
    # Local was attempted ONCE — and that's the retry, NOT the local-first slot.
    gw._t2.reason.assert_called_once()
    assert text == "local fallback"
    assert used_api is False
    assert (
        gw.last_tier == "local/retry"
    )  # not "local/interactive" — that would be local-first


# ── Background (non-user) turn still goes local-first ────────────────────────


def test_background_turn_routes_local_first():
    """is_user_turn=False (background processing, reading, etc.) → local-first
    is the intended path. D254 only mandates cloud-first for HUMAN turns."""
    gw = _make_gateway()
    gw._t2.reason.return_value = ("background work", 0.0)
    cortex = _make_cortex()

    text, cost, used_api = gw.reason(
        "background ne pass",
        [],
        [],
        level="interactive",
        cortex=cortex,
        is_user_turn=False,
        complexity="low",
    )

    gw._t2.reason.assert_called_once()
    gw._t4.reason.assert_not_called()
    assert used_api is False
    assert gw.last_tier == "local/interactive"  # local-first was the path

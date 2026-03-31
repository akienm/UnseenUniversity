"""
tests/test_inference_gateway_mode.py

Unit tests for D268: min_tier MODE override in InferenceGateway.

Verifies:
- When TWM has no mode_override entry: normal routing (Ollama for non-user turns)
- When TWM has min_tier=tier.4: _force_cloud_mode=True, Ollama skipped
- When TWM entry has different content: no override
- Error reading TWM: logs error, does not force cloud (safe default)
"""

import pytest
from unittest.mock import MagicMock, patch, call


def _make_gateway():
    """Build a minimal InferenceGateway with mock tier reasoners."""
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


def _make_cortex(mode_entries=None):
    """Mock cortex that returns given TWM entries for category=mode_override."""
    cortex = MagicMock()
    cortex.twm_read.return_value = mode_entries or []
    return cortex


# ── Test 1: no mode_override entry → Ollama used for non-user turn ───────────


def test_no_mode_override_uses_ollama_for_background():
    gw = _make_gateway()
    gw._t2.reason.return_value = ("hello", 0.0)
    cortex = _make_cortex([])

    text, cost, used_api = gw.reason(
        "hello",
        [],
        [],
        level="interactive",
        cortex=cortex,
        is_user_turn=False,
        complexity="low",
    )

    gw._t2.reason.assert_called_once()
    assert used_api is False
    assert gw.last_tier == "local/interactive"


# ── Test 2: min_tier=tier.4 in TWM → Ollama skipped, cloud used ──────────────


def test_mode_override_tier4_skips_ollama():
    gw = _make_gateway()
    gw._t4.reason.return_value = ("cloud response", 0.005)
    cortex = _make_cortex(
        [
            {
                "id": 1,
                "content_csb": "MODE|reading_bootstrap|min_tier=tier.4",
                "category": "mode_override",
            }
        ]
    )

    text, cost, used_api = gw.reason(
        "analyze this doc",
        [],
        [],
        level="interactive",
        cortex=cortex,
        is_user_turn=False,
        complexity="low",
    )

    gw._t2.reason.assert_not_called()
    gw._t4.reason.assert_called_once()
    assert used_api is True
    assert cost == 0.005


# ── Test 3: mode entry without min_tier=tier.4 → no override ─────────────────


def test_mode_override_different_content_no_force():
    gw = _make_gateway()
    gw._t2.reason.return_value = ("local", 0.0)
    cortex = _make_cortex(
        [
            {
                "id": 1,
                "content_csb": "MODE|some_other_mode|min_tier=tier.2",
                "category": "mode_override",
            }
        ]
    )

    text, cost, used_api = gw.reason(
        "hello",
        [],
        [],
        level="interactive",
        cortex=cortex,
        is_user_turn=False,
        complexity="low",
    )

    gw._t2.reason.assert_called_once()
    gw._t4.reason.assert_not_called()


# ── Test 4: TWM read raises exception → log error, no force cloud ─────────────


def test_mode_override_twm_error_safe_default():
    gw = _make_gateway()
    gw._t2.reason.return_value = ("local fallback", 0.0)
    cortex = MagicMock()
    cortex.twm_read.side_effect = RuntimeError("db gone")

    with patch("wild_igor.igor.cognition.inference_gateway.log_error") as mock_log:
        text, cost, used_api = gw.reason(
            "hello",
            [],
            [],
            level="interactive",
            cortex=cortex,
            is_user_turn=False,
            complexity="low",
        )

    mock_log.assert_called_once()
    assert mock_log.call_args[1]["kind"] == "MODE_READ_FAIL"
    gw._t2.reason.assert_called_once()
    gw._t4.reason.assert_not_called()


# ── Test 5: cortex=None → no TWM read, no override ───────────────────────────


def test_no_cortex_no_override():
    gw = _make_gateway()
    gw._t2.reason.return_value = ("local", 0.0)

    text, cost, used_api = gw.reason(
        "hello",
        [],
        [],
        level="interactive",
        cortex=None,
        is_user_turn=False,
        complexity="low",
    )

    gw._t2.reason.assert_called_once()
    gw._t4.reason.assert_not_called()

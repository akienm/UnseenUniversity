"""Tests for inference device tier escalation handoff.

Verifies:
- escalation_hop >= 2 raises RuntimeError (hard ceiling)
- escalation_hop > 0 with prior_attempt prepends structured handoff to system
- hop=0 (normal request) passes through unchanged
- tier transition is logged at INFO
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.inference.shim import InferenceRequest, InferenceResponse


def _make_device():
    from unseen_university.devices.inference.device import InferenceDevice
    from unseen_university.devices.inference.models_registry import default_registry
    d = InferenceDevice.__new__(InferenceDevice)
    d._blocked = False
    d._block_reason = ""
    d._mode = "openrouter"
    d._rules = MagicMock()
    d._rules.route.return_value = None  # fall through to legacy mode
    d._sources = {}
    d._models = default_registry()
    return d


def _fake_raw() -> dict:
    """OpenAI-compatible raw response dict for _parse_response."""
    return {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "model": "test-model",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class TestEscalationCeiling:
    def test_hop_2_raises(self):
        """Requests with escalation_hop >= 2 are rejected immediately."""
        d = _make_device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "help"}],
            escalation_hop=2,
            prior_attempt="prior text",
        )
        with pytest.raises(RuntimeError, match="ceiling"):
            d.dispatch(req)

    def test_hop_3_also_raises(self):
        d = _make_device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "help"}],
            escalation_hop=3,
            prior_attempt="prior text",
        )
        with pytest.raises(RuntimeError, match="ceiling"):
            d.dispatch(req)

    def test_hop_0_passes_through(self):
        """Normal (non-escalation) requests are not affected."""
        d = _make_device()
        req = InferenceRequest(messages=[{"role": "user", "content": "help"}])
        mock_source = MagicMock()
        mock_source.call.return_value = _fake_raw()
        d._sources = {"openrouter": mock_source}

        with patch("unseen_university.devices.inference.pattern_intercept.try_intercept", return_value=None), \
             patch("unseen_university.devices.inference.budget_ledger.check_session_limit"), \
             patch("unseen_university.devices.inference.budget_ledger.debit"):
            resp = d.dispatch(req)

        assert resp.text == "ok"
        call_args = mock_source.call.call_args[0][0]
        assert "Prior attempt summary" not in (call_args.system or "")


class TestEscalationHandoff:
    def test_hop_1_prepends_summary_to_system(self):
        """Hop 1 with prior_attempt inserts structured handoff into system prompt."""
        d = _make_device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "finish the task"}],
            system="Base system prompt.",
            escalation_hop=1,
            prior_attempt="I tried approach X, got stuck at Y",
        )
        mock_source = MagicMock()
        mock_source.call.return_value = _fake_raw()
        d._sources = {"openrouter": mock_source}

        with patch("unseen_university.devices.inference.pattern_intercept.try_intercept", return_value=None), \
             patch("unseen_university.devices.inference.budget_ledger.check_session_limit"), \
             patch("unseen_university.devices.inference.budget_ledger.debit"):
            d.dispatch(req)

        call_args = mock_source.call.call_args[0][0]
        sys_sent = call_args.system or ""
        assert "Base system prompt." in sys_sent
        assert "Prior attempt summary" in sys_sent
        assert "I tried approach X, got stuck at Y" in sys_sent
        assert "What now?" in sys_sent

    def test_hop_1_no_prior_attempt_no_prepend(self):
        """Hop 1 without prior_attempt does not inject anything."""
        d = _make_device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "help"}],
            system="Clean system.",
            escalation_hop=1,
            prior_attempt="",
        )
        mock_source = MagicMock()
        mock_source.call.return_value = _fake_raw()
        d._sources = {"openrouter": mock_source}

        with patch("unseen_university.devices.inference.pattern_intercept.try_intercept", return_value=None), \
             patch("unseen_university.devices.inference.budget_ledger.check_session_limit"), \
             patch("unseen_university.devices.inference.budget_ledger.debit"):
            d.dispatch(req)

        call_args = mock_source.call.call_args[0][0]
        assert "Prior attempt summary" not in (call_args.system or "")

    def test_tier_transition_logged_at_info(self, caplog):
        """Hop 1 escalation logs INFO with hop number and summary length."""
        import logging
        d = _make_device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "help"}],
            escalation_hop=1,
            prior_attempt="short summary",
        )
        mock_source = MagicMock()
        mock_source.call.return_value = _fake_raw()
        d._sources = {"openrouter": mock_source}

        with caplog.at_level(logging.INFO, logger="unseen_university.devices.inference.device"), \
             patch("unseen_university.devices.inference.pattern_intercept.try_intercept", return_value=None), \
             patch("unseen_university.devices.inference.budget_ledger.check_session_limit"), \
             patch("unseen_university.devices.inference.budget_ledger.debit"):
            d.dispatch(req)

        assert any(
            "tier-escalation" in r.message and "hop=1" in r.message
            for r in caplog.records
        ), f"Expected tier-escalation INFO log, got: {[r.message for r in caplog.records]}"

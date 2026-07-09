"""Tests for inference device tier escalation handoff.

Verifies:
- the hop ceiling is DERIVED from the capability ladder (one hop per rung above the seed)
- escalation_hop > 0 with prior_attempt prepends structured handoff to system
- hop=0 (normal request) passes through unchanged
- tier transition is logged at INFO
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.inference.routing_buckets import DIFFICULTY_BUCKETS
from unseen_university.devices.inference.shim import InferenceRequest, InferenceResponse


def _MAX_HOPS() -> int:
    """Mirror of device.py's derived ceiling: one hop per rung above the seed."""
    return len(DIFFICULTY_BUCKETS) - 1


def _make_device():
    from unseen_university.devices.inference.device import InferenceDevice
    from unseen_university.devices.inference.models_registry import default_registry
    d = InferenceDevice.__new__(InferenceDevice)
    d._blocked = False
    d._block_reason = ""
    d._mode = "openrouter"
    d._rules = MagicMock()
    # resolve() is the resolver entry point post-cutover (route() is deleted); returning
    # None makes the domain.select() path yield no decision so dispatch falls through to
    # the legacy single-source mode these escalation-handoff tests exercise.
    d._rules.resolve.return_value = None
    d._sources = {}
    d._models = default_registry()
    d._monitor = MagicMock()  # ResourceMonitor stub — dispatch records observed latency
    return d


def _fake_raw() -> dict:
    """OpenAI-compatible raw response dict for _parse_response."""
    return {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "model": "test-model",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class TestEscalationCeiling:
    """The hop ceiling is DERIVED from the capability ladder, not a magic number.

    It was hardcoded to 2 while DIFFICULTY_BUCKETS had three entries. Adding the `frontier`
    rung (T-inference-cost-first-sort-strands-cloud-fleet) would have left the cap behind, and
    hop 2 — the very hop that reaches above the local box — would have raised RuntimeError.
    BaseDomain._run_attempt catches a raise as AVAILABILITY, so the walk would have retried the
    same rung until it exhausted, and never escalated at all. A silent, expensive no-op.
    """

    def test_hop_2_reaches_the_frontier_rung_and_does_not_raise(self):
        """code -> design -> frontier: hop 2 is a legitimate rung, not the ceiling."""
        assert _MAX_HOPS() >= 3, "the ladder has 4 buckets; hop 2 must be dispatchable"
        d = _make_device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "help"}],
            escalation_hop=2,
            prior_attempt="prior text",
        )
        try:
            d.dispatch(req)  # may fail for lack of a live source; must NOT raise 'ceiling'
        except RuntimeError as exc:
            assert "ceiling" not in str(exc), f"hop 2 hit the escalation ceiling: {exc}"
        except Exception:
            pass  # no live source in the synthetic rack — not what this test is about

    def test_hop_past_the_top_rung_raises(self):
        """The walk must still terminate: one hop past the last bucket is the ceiling."""
        d = _make_device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "help"}],
            escalation_hop=_MAX_HOPS(),
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
        # The proxy prepends the domain's handoff verbatim under a header that names it a
        # FAILURE. The old header ("Prior attempt summary" / "What was tried:" / "What now?")
        # presented a failed attempt as useful prior work, and the stronger model continued
        # the weaker one's reasoning — measured live, T-escalation-handoff-transmits-the-
        # confabulation. The proxy no longer frames the handoff at all; the escalation walk,
        # which is the only thing that knows the attempt failed, frames it.
        assert "Failed prior attempt (escalation hop 1)" in sys_sent
        assert "I tried approach X, got stuck at Y" in sys_sent
        assert "What was tried:" not in sys_sent

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

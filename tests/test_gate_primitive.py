"""
test_gate_primitive.py — T-inhibitory-pattern-primitive

Tests for gate primitive: competing signals in TWM.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_gate_memory(gate_id="GATE_TEST", domain="test", code_ref=None, **extra):
    """Create a mock Memory with gate metadata."""
    m = MagicMock()
    m.id = gate_id
    meta = {
        "gate": True,
        "habit_type": "gate",
        "gate_domain": domain,
    }
    if code_ref:
        meta["code_ref"] = code_ref
    meta.update(extra)
    m.metadata = meta
    return m


def _make_cortex():
    """Create a mock cortex with twm_push and write_ring."""
    cortex = MagicMock()
    cortex.twm_push.return_value = 42
    cortex.twm_read.return_value = []
    return cortex


class TestEvaluateGate:
    def test_unconditional_gate_fires(self):
        from wild_igor.igor.cognition.gate_primitive import evaluate_gate

        gate = _make_gate_memory()
        cortex = _make_cortex()
        ctx = {"user_input": "test", "turn_id": "t1"}

        should_gate, reason = evaluate_gate(gate, cortex, ctx)
        assert should_gate is True
        assert "unconditional_gate" in reason

    def test_code_ref_evaluator_returning_tuple(self):
        from wild_igor.igor.cognition.gate_primitive import evaluate_gate

        gate = _make_gate_memory(code_ref="mod:check_fn")
        cortex = _make_cortex()

        mock_tool = MagicMock()
        mock_tool.execute.return_value = (True, "claim unverified")

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_tool

        with patch("lab.utility_closet.registry.registry", mock_registry):
            should_gate, reason = evaluate_gate(gate, cortex, {"user_input": "test"})
        assert should_gate is True
        assert reason == "claim unverified"

    def test_code_ref_evaluator_returning_dict(self):
        from wild_igor.igor.cognition.gate_primitive import evaluate_gate

        gate = _make_gate_memory(code_ref="mod:check_fn")
        cortex = _make_cortex()

        mock_tool = MagicMock()
        mock_tool.execute.return_value = {"gated": False, "reason": "all clear"}

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_tool

        with patch("lab.utility_closet.registry.registry", mock_registry):
            should_gate, reason = evaluate_gate(gate, cortex, {"user_input": "test"})
        assert should_gate is False
        assert reason == "all clear"

    def test_code_ref_error_fails_open(self):
        from wild_igor.igor.cognition.gate_primitive import evaluate_gate

        gate = _make_gate_memory(code_ref="mod:broken_fn")
        cortex = _make_cortex()

        mock_tool = MagicMock()
        mock_tool.execute.side_effect = RuntimeError("boom")

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_tool

        with patch("lab.utility_closet.registry.registry", mock_registry):
            should_gate, reason = evaluate_gate(gate, cortex, {"user_input": "test"})
        assert should_gate is False
        assert "evaluator_error" in reason


class TestFireGate:
    def test_pushes_to_twm(self):
        from wild_igor.igor.cognition.gate_primitive import fire_gate

        gate = _make_gate_memory(domain="action_claims")
        cortex = _make_cortex()
        ctx = {"turn_id": "t1", "thread_id": "th1"}

        obs_id = fire_gate(gate, cortex, ctx, "claim unverified")
        assert obs_id == 42
        cortex.twm_push.assert_called_once()
        call_kwargs = cortex.twm_push.call_args[1]
        assert call_kwargs["source"] == "gate:GATE_TEST"
        assert call_kwargs["category"] == "gate_signal"
        assert call_kwargs["salience"] == 0.92
        assert call_kwargs["urgency"] == 0.85
        assert "GATE_SIGNAL" in call_kwargs["content_csb"]
        assert "action_claims" in call_kwargs["content_csb"]

    def test_custom_salience(self):
        from wild_igor.igor.cognition.gate_primitive import fire_gate

        gate = _make_gate_memory(gate_salience=0.7, gate_urgency=0.5)
        cortex = _make_cortex()

        fire_gate(gate, cortex, {"turn_id": "t1"}, "test")
        call_kwargs = cortex.twm_push.call_args[1]
        assert call_kwargs["salience"] == 0.7
        assert call_kwargs["urgency"] == 0.5

    def test_logs_to_ring(self):
        from wild_igor.igor.cognition.gate_primitive import fire_gate

        gate = _make_gate_memory()
        cortex = _make_cortex()

        fire_gate(gate, cortex, {"turn_id": "t1"}, "test reason")
        cortex.write_ring.assert_called_once()
        ring_msg = cortex.write_ring.call_args[0][0]
        assert "GATE_FIRED" in ring_msg
        assert "GATE_TEST" in ring_msg


class TestDispatchGate:
    def test_full_dispatch_gated(self):
        from wild_igor.igor.cognition.gate_primitive import dispatch_gate

        gate = _make_gate_memory(domain="coherence")
        cortex = _make_cortex()
        ctx = {"user_input": "test", "turn_id": "t1"}

        result = dispatch_gate(gate, cortex, ctx)
        assert result["gated"] is True
        assert result["gate_id"] == "GATE_TEST"
        assert result["domain"] == "coherence"
        assert result["obs_id"] == 42

    def test_full_dispatch_not_gated(self):
        from wild_igor.igor.cognition.gate_primitive import dispatch_gate

        gate = _make_gate_memory(code_ref="mod:check_fn")
        cortex = _make_cortex()

        mock_tool = MagicMock()
        mock_tool.execute.return_value = (False, "all clear")
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_tool

        with patch("lab.utility_closet.registry.registry", mock_registry):
            result = dispatch_gate(gate, cortex, {"user_input": "test"})
        assert result["gated"] is False
        assert result["obs_id"] is None
        cortex.twm_push.assert_not_called()


class TestGateEngramNode:
    def test_no_gate_signals_passes(self):
        from wild_igor.igor.cognition.inhibition_chain import GateEngramNode

        node = GateEngramNode()
        cortex = _make_cortex()
        cortex.twm_read.return_value = []
        basket = {"node_id": "SOME_HABIT"}

        inhibited, reason = node.check(basket, cortex)
        assert inhibited is False

    def test_active_gate_signal_inhibits(self):
        from wild_igor.igor.cognition.inhibition_chain import GateEngramNode

        node = GateEngramNode()
        cortex = _make_cortex()
        cortex.twm_read.return_value = [
            {
                "source": "gate:GATE_CONFAB",
                "content_csb": "GATE_SIGNAL|domain=action_claims|gate=GATE_CONFAB",
                "integrated": False,
                "expires_at": None,
                "metadata": {
                    "gate_id": "GATE_CONFAB",
                    "reason": "claim unverified",
                },
            }
        ]
        basket = {"node_id": "SOME_HABIT"}

        inhibited, reason = node.check(basket, cortex)
        assert inhibited is True
        assert "GATE_CONFAB" in reason
        assert basket.get("inhibition.gate_id") == "GATE_CONFAB"

    def test_expired_gate_signal_passes(self):
        from wild_igor.igor.cognition.inhibition_chain import GateEngramNode

        node = GateEngramNode()
        cortex = _make_cortex()
        cortex.twm_read.return_value = [
            {
                "source": "gate:GATE_OLD",
                "content_csb": "GATE_SIGNAL|domain=test",
                "integrated": False,
                "expires_at": "2020-01-01T00:00:00",
                "metadata": {"gate_id": "GATE_OLD", "reason": "old"},
            }
        ]
        basket = {"node_id": "SOME_HABIT"}

        inhibited, reason = node.check(basket, cortex)
        assert inhibited is False

    def test_integrated_gate_signal_ignored(self):
        from wild_igor.igor.cognition.inhibition_chain import GateEngramNode

        node = GateEngramNode()
        cortex = _make_cortex()
        cortex.twm_read.return_value = [
            {
                "source": "gate:GATE_DONE",
                "content_csb": "GATE_SIGNAL|domain=test",
                "integrated": True,
                "metadata": {"gate_id": "GATE_DONE", "reason": "done"},
            }
        ]
        basket = {"node_id": "SOME_HABIT"}

        inhibited, reason = node.check(basket, cortex)
        assert inhibited is False

    def test_twm_read_failure_passes(self):
        from wild_igor.igor.cognition.inhibition_chain import GateEngramNode

        node = GateEngramNode()
        cortex = _make_cortex()
        cortex.twm_read.side_effect = RuntimeError("db down")
        basket = {"node_id": "SOME_HABIT"}

        inhibited, reason = node.check(basket, cortex)
        assert inhibited is False


class TestDefaultChainIncludesGate:
    def test_gate_engram_node_in_chain(self):
        from wild_igor.igor.cognition.inhibition_chain import (
            GateEngramNode,
            default_chain,
        )

        chain = default_chain()
        gate_nodes = [n for n in chain.nodes if isinstance(n, GateEngramNode)]
        assert len(gate_nodes) == 1

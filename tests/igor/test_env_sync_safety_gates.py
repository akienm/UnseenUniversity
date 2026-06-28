"""T-safety-gates-above-env-sync — SAFETY_GATE_NAMES are file-only.

Pass-2 Area 4 P1-8.1 CONFIRMED_WORSE: IGOR_TIER5_ENABLED, IGOR_ARBITER_ENABLED,
IGOR_SELF_EDIT_ENABLED were round-tripping through the config graph via
env_sync, meaning any engram with cortex.store() access to SYSCFG_* nodes
could flip them invisibly. This test pins the fix: these gates are never
pushed and never hydrated.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.igor.env_sync import (
    SAFETY_GATE_NAMES,
    _is_safety_gate,
    hydrate_from_graph,
    push_vars_to_graph,
)


def test_safety_gate_names_set():
    """The three canonical gates must be in the skip-list."""
    assert "IGOR_TIER5_ENABLED" in SAFETY_GATE_NAMES
    assert "IGOR_ARBITER_ENABLED" in SAFETY_GATE_NAMES
    assert "IGOR_SELF_EDIT_ENABLED" in SAFETY_GATE_NAMES


def test_is_safety_gate_helper():
    assert _is_safety_gate("IGOR_TIER5_ENABLED") is True
    assert _is_safety_gate("IGOR_ARBITER_ENABLED") is True
    assert _is_safety_gate("IGOR_SELF_EDIT_ENABLED") is True
    assert _is_safety_gate("IGOR_NE_LOCAL_MODEL") is False
    assert _is_safety_gate("OPENROUTER_API_KEY") is False


class TestPushExcludesSafetyGates:
    def test_push_skips_tier5(self):
        cortex = MagicMock()
        with patch("unseen_university.devices.igor.env_sync._ensure_root_nodes", lambda c: None):
            count = push_vars_to_graph(
                cortex, {"IGOR_TIER5_ENABLED": "true", "OLLAMA_HOST": "x"}
            )
        # Only OLLAMA_HOST should have been pushed
        assert count == 1
        stored_memories = [call.args[0] for call in cortex.store.call_args_list]
        for mem in stored_memories:
            assert mem.metadata.get("env_key") != "IGOR_TIER5_ENABLED"

    def test_push_skips_all_three_gates(self):
        cortex = MagicMock()
        vars_dict = {
            "IGOR_TIER5_ENABLED": "true",
            "IGOR_ARBITER_ENABLED": "true",
            "IGOR_SELF_EDIT_ENABLED": "true",
            "OLLAMA_HOST": "localhost",
        }
        with patch("unseen_university.devices.igor.env_sync._ensure_root_nodes", lambda c: None):
            count = push_vars_to_graph(cortex, vars_dict)
        assert count == 1


class TestHydrateRefusesSafetyGates:
    def _make_cortex_with_safety_gate_node(self, key, value):
        """Build a MagicMock cortex whose get_children returns a safety-gate memory."""
        cortex = MagicMock()
        mem = MagicMock()
        mem.metadata = {"env_key": key, "env_value": value, "scope": "global"}

        def _get_children(cat_id):
            # Return the safety-gate node in the features category only
            from unseen_university.devices.igor.env_sync import SYSCFG_FEATURES_ID

            if cat_id == SYSCFG_FEATURES_ID:
                return [mem]
            return []

        cortex.get_children.side_effect = _get_children
        return cortex

    def test_hydrate_refuses_tier5(self, caplog, monkeypatch):
        monkeypatch.delenv("IGOR_TIER5_ENABLED", raising=False)
        cortex = self._make_cortex_with_safety_gate_node("IGOR_TIER5_ENABLED", "true")
        with caplog.at_level(logging.WARNING):
            filled = hydrate_from_graph(cortex)
        assert filled == 0
        assert "IGOR_TIER5_ENABLED" not in os.environ
        assert any(
            "refused to rehydrate safety gate" in rec.message for rec in caplog.records
        )

    def test_hydrate_still_applies_non_gate_vars(self, monkeypatch):
        monkeypatch.delenv("NOT_A_GATE_VAR_XYZ", raising=False)
        cortex = self._make_cortex_with_safety_gate_node(
            "NOT_A_GATE_VAR_XYZ", "somevalue"
        )
        filled = hydrate_from_graph(cortex)
        assert filled == 1
        assert os.environ.get("NOT_A_GATE_VAR_XYZ") == "somevalue"
        # Cleanup
        monkeypatch.delenv("NOT_A_GATE_VAR_XYZ", raising=False)

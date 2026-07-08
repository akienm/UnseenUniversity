"""Tests for the model<->provider connections stack (T-inference-connections-stack).

The connections stack (stack 3 of 4, D-inference-router-stack-decomposition-2026-07-08)
makes model<->provider reachability a first-class edge instead of the monolith's 1:1
ModelSpec.source_name binding. These tests pin: a model can carry N connections,
by_model isolates per model, and a connection carries per-pairing cost.
"""

from __future__ import annotations

from unseen_university.devices.inference.connections import (
    Connection,
    ConnectionsRegistry,
)


# seed_from_models is deleted at the router cutover — reachability now lives on the
# authoritative default_connections table, not a 1:1 snapshot of the (also deleted)
# ModelSpec.source_name binding, so its two tests are retired.


def test_model_can_have_multiple_connections():
    """A model_id may be reachable on N providers — the fact the monolith could not express."""
    reg = ConnectionsRegistry()
    reg.register(Connection("deepseek-v4-flash", "openrouter", 0.0000028))
    reg.register(Connection("deepseek-v4-flash", "ollama_cloud", 0.0))

    conns = reg.by_model("deepseek-v4-flash")
    assert len(conns) == 2
    assert {c.source_name for c in conns} == {"openrouter", "ollama_cloud"}


def test_by_model_returns_empty_for_unknown():
    """by_model is fail-soft: an unregistered model_id yields an empty list, not a raise."""
    reg = ConnectionsRegistry()
    assert reg.by_model("no-such-model") == []


def test_connection_carries_per_pairing_cost():
    """Cost lives on the connection (per provider), explicitly set at registration."""
    reg = ConnectionsRegistry()
    reg.register(Connection("m", "openrouter", 0.5))
    matches = reg.by_model("m")
    assert matches, "no connection for registered model"
    assert matches[0].dollars_per_unit == 0.5

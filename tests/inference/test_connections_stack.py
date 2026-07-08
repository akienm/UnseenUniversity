"""Tests for the model<->provider connections stack (T-inference-connections-stack).

The connections stack (stack 3 of 4, D-inference-router-stack-decomposition-2026-07-08)
makes model<->provider reachability a first-class edge instead of the monolith's 1:1
ModelSpec.source_name binding. These tests pin: (1) seed_from_models mirrors the current
bindings exactly, (2) a model can carry N connections, (3) by_model isolates per model,
and (4) a connection carries per-pairing cost.
"""

from __future__ import annotations

from unseen_university.devices.inference.connections import (
    Connection,
    ConnectionsRegistry,
    seed_from_models,
)
from unseen_university.devices.inference.models_registry import ModelSpec, default_registry


def test_seed_mirrors_current_model_bindings():
    """seed_from_models produces exactly one connection per ModelSpec, same (model, source)."""
    models = default_registry()
    conns = seed_from_models(models)

    specs = models.all()
    # One connection per model row — the faithful 1:1 snapshot.
    assert len(conns.all()) == len(specs)

    # Every ModelSpec's (model_id, source_name) is represented by a connection, and the
    # per-connection cost equals the spec's dollars_per_unit.
    seeded = {(c.model_id, c.source_name): c for c in conns.all()}
    for spec in specs:
        key = (spec.model_id, spec.source_name)
        assert key in seeded, f"missing connection for {key}"
        assert seeded[key].dollars_per_unit == spec.dollars_per_unit


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
    """Cost lives on the connection (per provider), not smeared onto the model."""
    models = default_registry()
    conns = seed_from_models(models)
    spec = models.all()[0]
    matches = [c for c in conns.by_model(spec.model_id) if c.source_name == spec.source_name]
    assert matches, f"no connection for seeded model {spec.model_id}"
    assert matches[0].dollars_per_unit == spec.dollars_per_unit

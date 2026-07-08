"""
Proof for T-inference-migrate-consumers-cutover (5/6 of D-inference-router-stack-decomposition).

The cutover repointed every consumer onto the dimensional resolver (device.py dispatch ->
domain.select() -> RulesEngine.resolve()) and DELETED the monolith: the (task_class,
model_id, source_name) rule triples (_DEFAULT_RULES + RoutingRule), route(), add_compiled_rule,
and the ModelSpec.source_name 1:1 binding. Reachability now lives solely on the connections
stack (the authoritative default table).

This proof is red on the pre-cutover code and green after, on assertions a hollow build could
not pass:

  (A) The monolith is GONE — RoutingRule/_DEFAULT_RULES/route()/add_compiled_rule no longer
      exist, and ModelSpec has no source_name field (no model/provider literal triple can
      survive; the decision's git-grep signal, checked structurally).
  (B) The live proxy still resolves a real dispatch (the migration never broke dispatch).
  (C) The TRIPLE-ONLY Hex edge (devstral-small-2:24b@ollama) survives into what the live
      resolver actually reads — the reachability the grep-proof CANNOT see (seeding from the
      deleted source_name alone would silently drop it).
  (D) The coding ESCALATION LADDER is preserved: a worker/coding request bumped to design
      difficulty resolves to a design-capable owned-local model, NOT None+system_alarm. This
      discriminates the device's policies=[] wiring from the policies=None trap (which
      false-halts the ladder — the one thing the coding domain exists to do).
  (E) Every default model is reachable (>=1 connection) — guards the explicit 24-edge table
      against drifting out of sync with the model registry.
"""

from __future__ import annotations

import dataclasses

import unseen_university.devices.inference.rules_engine as rules_engine_mod
from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.dimensions import RouteRequest
from unseen_university.devices.inference.models_registry import ModelSpec
from unseen_university.devices.inference.rules_engine import RulesEngine


def test_consumers_cutover_proof():
    # (A) The monolith is deleted — no triple / route() / compiled-rule path survives.
    assert not hasattr(rules_engine_mod, "_DEFAULT_RULES"), "the rule triples must be gone"
    assert not hasattr(rules_engine_mod, "RoutingRule"), "the RoutingRule triple type must be gone"
    assert not hasattr(RulesEngine, "route"), "route() (the monolith entry point) must be gone"
    assert not hasattr(RulesEngine, "add_compiled_rule"), "the triple-emitting path must be gone"
    field_names = {f.name for f in dataclasses.fields(ModelSpec)}
    assert "source_name" not in field_names, (
        "ModelSpec.source_name (the 1:1 model<->provider binding) must be deleted — "
        "reachability lives on the connections stack now"
    )

    # The live proxy wiring (device.py) drives the resolver.
    dev = InferenceDevice()
    try:
        eng = dev._rules
        coding = RouteRequest(
            ticket_tier="builder", builder_tier="builder", domain="coding", urgency="normal",
        )

        # (B) live dispatch still resolves a concrete (provider, model).
        seed = eng.resolve(coding)
        assert seed is not None, "the migrated live proxy must still resolve a dispatch"

        # (C) the triple-only Hex edge survives into what the live resolver reads.
        live_edges = {
            (c.model_id, c.source_name) for c in eng._resolve_connections().all()
        }
        assert ("devstral-small-2:24b", "ollama") in live_edges, (
            "the triple-only owned-local Hex edge must survive the cutover — "
            "the reachability the grep-proof cannot catch"
        )
        assert ("qwen3-coder-next", "ollama") in live_edges

        # (D) the coding escalation ladder is preserved (design rung serves, no false-halt).
        walked = RouteRequest(
            ticket_tier="builder", builder_tier="builder", domain="coding",
            urgency="normal", escalation_allowed=True,
        )
        design = eng.resolve(walked, required_difficulty="design")
        assert design is not None, (
            "the coding escalation ladder must NOT false-halt at the design rung — "
            "device.py wires policies=[] precisely so a design-capable owned-local model "
            "serves the bumped request (policies=None would fire a spurious system_alarm)"
        )
        assert design.model.difficulty_bucket == "design"
        assert design.source.cost_class == "owned_local"

        # (E) every default model is reachable — the explicit edge table stays in sync.
        conns = eng._resolve_connections()
        orphans = [s.model_id for s in dev._models.all() if not conns.by_model(s.model_id)]
        assert not orphans, f"every model must have >=1 connection; orphaned: {orphans}"
    finally:
        # HealthMonitor spins a daemon thread on construction; stop it so the test is tidy.
        try:
            dev._health.stop()
        except Exception:
            pass

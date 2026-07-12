"""
Proof for T-inference-migrate-consumers-cutover (5/6 of D-inference-router-stack-decomposition).

The cutover repointed every consumer onto the dimensional resolver (device.py dispatch ->
domain.select() -> RulesEngine.resolve()) and DELETED the monolith: the (task_class,
model_id, source_name) rule triples (_DEFAULT_RULES + RoutingRule), route(),
add_compiled_rule, and the ModelSpec.source_name 1:1 binding. Reachability now lives solely
on the connections stack.

THE DEFECT THIS PROOF PINS (found by running the live coding path, not by reading code):
the monolith's triples hand-curated devstral-small-2:24b as the coding floor. Dimensional
selection reads the STRUCTURED `features` flag instead — and devstral's flag was never
populated, even though its own catalog row already recorded `agentic` / `tool-call` tags.
With the coding-needs-tools policy off, coding therefore resolved to a tool-less REASONING
model (deepseek-r1:14b), which cannot emit a tool call: it returned
`{"status": "done", "result": "wrote smoke file"}` and wrote NOTHING. A builder that
confabulates success is strictly worse than one that fails, so the invariant below is not
"the right model wins" (a curation preference) but:

  ==> a CODING request must resolve to a TOOL-CAPABLE model, so a coding failure HALTS
      instead of silently claiming success.

Deliberately HERMETIC: it builds the engine from the default registries directly and never
constructs an InferenceDevice(), whose HealthMonitor probes real providers (Hex, cloud keys)
on a background thread. An earlier version of this proof asserted through a live device and
passed only because this box happened to have Hex up — a proof that depends on the weather
is not a proof.
"""

from __future__ import annotations

import dataclasses

import unseen_university.devices.inference.rules_engine as rules_engine_mod
from unseen_university.devices.inference.connections import default_connections
from unseen_university.devices.inference.device import _default_models, _default_sources
from unseen_university.devices.inference.dimensions import RouteRequest
from unseen_university.devices.inference.models_registry import ModelSpec
from unseen_university.devices.inference.rules_engine import (
    OUTCOME_NO_CAPABLE_MODEL,
    RulesEngine,
)

#: The purpose-built agentic coding model the monolith curated as the coding floor.
AGENTIC_CODER = "devstral-small-2:24b"


def _hermetic_engine():
    """The default rack, wired exactly as device.py wires it — but with no live device."""
    sources = _default_sources()
    models = _default_models()
    return RulesEngine(
        sources,
        models,
        connections=default_connections(models),
        policies=None,  # _DEFAULT_POLICIES — coding-needs-tools is load-bearing
    ), models


def test_consumers_cutover_proof():
    # ── (A) The monolith is deleted — no triple / route() / compiled-rule path survives.
    assert not hasattr(rules_engine_mod, "_DEFAULT_RULES"), "the rule triples must be gone"
    assert not hasattr(rules_engine_mod, "RoutingRule"), "the RoutingRule triple type must be gone"
    assert not hasattr(RulesEngine, "route"), "route() (the monolith entry point) must be gone"
    assert not hasattr(RulesEngine, "add_compiled_rule"), "the triple-emitting path must be gone"
    field_names = {f.name for f in dataclasses.fields(ModelSpec)}
    assert "source_name" not in field_names, (
        "ModelSpec.source_name (the 1:1 model<->provider binding) must be deleted — "
        "reachability lives on the connections stack now"
    )

    engine, models = _hermetic_engine()

    # ── (B) Recorded capability must be mirrored into the flag the SELECTOR reads.
    # The free-text tags were the evidence; `features` is what routing actually filters on.
    # Leaving them out of sync is what let a tool-less model take the coding slot.
    for spec in models.all():
        if "tool-call" in (spec.tags or []):
            assert "tools" in (spec.features or []), (
                f"{spec.model_id} records a 'tool-call' tag but its structured features flag "
                f"is {spec.features!r} — the selector filters on `features`, so an unmirrored "
                f"capability makes the model invisible to a tools-requiring policy"
            )

    # ── (C) THE ANTI-CONFABULATION INVARIANT: coding resolves to a TOOL-CAPABLE model.
    # No required_difficulty passed pins the pick to the seed rung: deterministic, no alarm.
    coding = RouteRequest(
        ticket_tier="builder", builder_tier="builder", domain="coding", urgency="normal",
    )
    dec = engine.resolve(coding)
    assert dec is not None, "the live coding path must resolve a model"
    assert "tools" in (dec.model.features or []), (
        f"coding resolved to {dec.model.model_id!r} with features={dec.model.features!r} — a "
        f"model that cannot emit a tool call cannot build, and will confabulate completion "
        f"instead of halting"
    )
    # ...and the cheapest tool-capable owned-local coder is the purpose-built agentic one.
    assert dec.model.model_id == AGENTIC_CODER
    assert dec.source.cost_class == "owned_local"

    # ── (D) The triple-only Hex edges survive into the stack the resolver reads.
    # This is the reachability a `git grep` for the deleted triples CANNOT see: seeding
    # connections from the (now-deleted) source_name alone would silently drop them.
    live_edges = {(c.model_id, c.source_name) for c in engine._resolve_connections().all()}
    assert (AGENTIC_CODER, "ollama") in live_edges, (
        "the triple-only owned-local Hex edge must survive the cutover"
    )
    assert ("qwen3-coder-next", "ollama") in live_edges

    # ── (E) Every default model is reachable — the explicit edge table stays in sync
    # with the model registry (the drift risk an explicit table introduces).
    conns = engine._resolve_connections()
    orphans = [s.model_id for s in models.all() if not conns.by_model(s.model_id)]
    assert not orphans, f"every model must have >=1 connection; orphaned: {orphans}"


def test_coding_escalation_past_local_capability_is_an_honest_ceiling():
    """Bumping a CODING request to design difficulty finds no tool-capable design-level coder.

    resolve() returns a TYPED NO_CAPABLE_MODEL (not a tool-less model that would confabulate,
    and not an undifferentiated None). This is a real capability ceiling, honestly surfaced —
    NOT a routing bug. (The monolith escalated to deepseek-r1:32b here, which also has
    features=[] and also could not build; matching that was matching a broken rung.)

    The typing is what lets the domain walk tell this capability ceiling APART from an
    availability outage: it now maps NO_CAPABLE_MODEL to a capability failure (escalate a
    rung), no longer laundering it into AVAILABILITY and retrying a doomed rung before halting
    with a message that names the wrong cause. That was the CP3 bug
    (T-inference-capability-ceiling-misclassified-as-availability), and it is FIXED by the
    typed result this asserts (T-inference-typed-no-path-result).
    """
    engine, _ = _hermetic_engine()
    walked = RouteRequest(
        ticket_tier="builder", builder_tier="builder", domain="coding", urgency="normal",
    )
    assert engine.resolve(walked, required_difficulty="design").kind == OUTCOME_NO_CAPABLE_MODEL

"""
connections.py — the model<->provider CONNECTIONS stack for the inference router.

Stack 3 of 4 in the dimensional router (D-inference-router-stack-decomposition-2026-07-08).
A CONNECTION is a first-class edge: "model M is reachable on provider P at a
per-connection cost." A model may have N connections — the same logical model reachable
on several providers at different prices. That is the fact the monolithic router could
not express: it smeared model<->provider reachability across BOTH ModelSpec.source_name
(a 1:1 binding) AND the hardcoded (task_class, model_id, source_name) rule triples, so
adding a provider for an existing model meant editing rules.

Where the facts live in the 4-stack model:
  - providers stack (sources.py):     billing_type / cost_class / time_bucket / availability
  - models stack (models_registry.py): capability — difficulty ceiling, domains, features
  - connections stack (this file):     the model<->provider edge + that pairing's cost
  - rules stack:                        policy over dimensions -> capability envelope

This stack is ADDITIVE. Nothing routes through it yet: route() and _DEFAULT_RULES are
untouched, so live dispatch is unaffected. The resolver (T-inference-resolver-compose)
will consume it; the cutover (T-inference-migrate-consumers-cutover) removes
ModelSpec.source_name once connections are the authoritative home of reachability.
seed_from_models() mirrors the current 1:1 bindings so the stack starts as a faithful
snapshot of live reachability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from unseen_university.devices.inference.models_registry import ModelsRegistry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Connection:
    """A model<->provider edge: model_id reachable on source_name at dollars_per_unit.

    Economics (marginal cost) live HERE, not on the model — the same model can cost
    differently per provider (OpenRouter vs. an owned-local Ollama). Capability
    (difficulty / domains / features) stays on the model stack; billing_type /
    cost_class / time_bucket stay on the provider stack. A connection only JOINS a
    model to a provider and carries that pairing's marginal cost, so the resolver
    (T-inference-resolver-compose) can price each reachable path independently.
    """

    model_id: str
    source_name: str
    dollars_per_unit: float = 0.0


class ConnectionsRegistry:
    """The connections stack: model_id -> [Connection, ...] (a model may have N).

    In-memory registry (flat data, no persistence) mirroring the SourceRegistry /
    ModelsRegistry pattern. A model_id accumulates one Connection per provider it is
    reachable on; by_model() returns them all so the resolver can pick the cheapest
    available path.
    """

    def __init__(self) -> None:
        self._by_model: dict[str, list[Connection]] = {}

    def register(self, conn: Connection) -> None:
        """Add one connection. A model_id may accumulate several (different providers)."""
        self._by_model.setdefault(conn.model_id, []).append(conn)
        log.debug(
            "connections: registered %s@%s ($%.6f/unit)",
            conn.model_id,
            conn.source_name,
            conn.dollars_per_unit,
        )

    def by_model(self, model_id: str) -> list[Connection]:
        """All connections for a model_id (a fresh list; empty when none)."""
        return list(self._by_model.get(model_id, []))

    def all(self) -> list[Connection]:
        """Every connection across every model."""
        return [c for conns in self._by_model.values() for c in conns]


def seed_from_models(models: ModelsRegistry) -> ConnectionsRegistry:
    """Build a connections stack mirroring the current 1:1 ModelSpec.source_name bindings.

    One connection per ModelSpec row (model_id -> its single source_name), carrying the
    spec's dollars_per_unit. This is the faithful snapshot the additive stack starts
    from; at cutover the source_name binding is removed and connections become the sole
    home of model<->provider reachability.
    """
    reg = ConnectionsRegistry()
    for spec in models.all():
        reg.register(
            Connection(
                model_id=spec.model_id,
                source_name=spec.source_name,
                dollars_per_unit=spec.dollars_per_unit,
            )
        )
    log.info(
        "connections: seeded %d connection(s) from %d model(s)",
        len(reg.all()),
        len(models.all()),
    )
    return reg

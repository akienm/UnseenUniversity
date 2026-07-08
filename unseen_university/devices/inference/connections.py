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

This stack is LIVE: the dimensional resolver (rules_engine.resolve) composes it for every
routing decision. The pre-cutover monolith — route() and _DEFAULT_RULES — has been deleted,
and ModelSpec.source_name is gone, so this stack is now the AUTHORITATIVE (and sole) home of
model<->provider reachability. default_connections() builds the authoritative edge table;
it is self-contained (it does not read a source_name off the model, because there is none).
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


# ── The authoritative connection table (D-inference-router-stack-decomposition) ──
# After the cutover (T-inference-migrate-consumers-cutover) this is the SOLE home of
# model<->provider reachability: ModelSpec.source_name is deleted, so nothing can derive
# an edge from the model stack anymore. The table therefore carries the (model_id,
# source_name) pairs EXPLICITLY — it must stay self-contained (no source_name read).
#
# It is the functional UNION of what the monolith smeared across TWO places: the 1:1
# ModelSpec.source_name binding AND the (task_class, model_id, source_name) rule triples.
# The triples carried reachability the source_name binding could not — a model reachable
# on a SECOND provider. Two such edges (the Hex-owned-local twins of cloud models) existed
# ONLY in the triples; seeding from source_name alone silently drops them (the landmine the
# grep-proof cannot catch), so they are pinned here explicitly and marked:
#   devstral-small-2:24b@ollama, qwen3-coder-next@ollama  (owned-local Hex; triple-only).
# The phantom triple qwen/qwen3-30b-a3b-instruct@openrouter is intentionally ABSENT — it has
# no ModelSpec (creator tier disabled), so resolve() (which iterates models.all()) could never
# reach it; carrying it would be dead debt.
_DEFAULT_CONNECTION_EDGES: list[tuple[str, str]] = [
    ("anthropic/claude-haiku-4.5", "openrouter"),
    ("anthropic/claude-opus-4.8", "openrouter"),
    ("anthropic/claude-sonnet-4.6", "openrouter"),
    ("claude-sonnet-4-6", "anthropic"),
    ("deepseek-r1:14b", "ollama"),
    ("deepseek-r1:32b", "ollama"),
    ("deepseek-v3.1:671b-cloud", "ollama_cloud"),
    ("deepseek-v4-flash", "ollama_cloud"),
    ("deepseek/deepseek-v4-flash", "openrouter"),
    ("devstral-small-2:24b", "ollama_cloud"),
    ("devstral-small-2:24b", "ollama"),  # triple-only Hex edge — MUST survive the cutover
    ("gemini-2.0-flash-paid", "google"),
    ("gemini-2.5-flash", "google_free"),
    ("google/gemini-2.0-flash", "openrouter"),
    ("llama3.2:3b", "ollama"),
    ("qwen/qwen3-235b-a22b-2507", "openrouter"),
    ("qwen/qwen3-coder", "openrouter"),
    ("qwen/qwen3-coder-30b-a3b-instruct", "openrouter"),
    ("qwen/qwen3.5-9b", "openrouter"),
    ("qwen2.5-coder:14b", "ollama"),
    ("qwen3-coder-next", "ollama_cloud"),
    ("qwen3-coder-next", "ollama"),  # triple-only Hex edge — MUST survive the cutover
    ("qwen3-coder:30b", "ollama"),
    ("qwen3-coder:480b-cloud", "ollama_cloud"),
]


def default_connections(models: ModelsRegistry) -> ConnectionsRegistry:
    """The authoritative connections stack: the explicit _DEFAULT_CONNECTION_EDGES table.

    This is the SOLE home of reachability after the cutover. Each edge's marginal cost is
    read from the model's `dollars_per_unit` at build time (dollars_per_unit stays on the
    model stack for now — relocating economics fully onto the connection is a separate
    concern; the source's cost_class already separates owned-local from cloud, the selector's
    first sort key). A phantom edge whose model_id has no ModelSpec resolves to $0 and is
    harmless — resolve() iterates models.all() and never reaches it — but the table carries
    none by construction.
    """
    reg = ConnectionsRegistry()
    for model_id, source_name in _DEFAULT_CONNECTION_EDGES:
        spec = models.get(model_id)
        dollars = spec.dollars_per_unit if spec is not None else 0.0
        reg.register(Connection(model_id, source_name, dollars))
    log.info(
        "connections: built %d authoritative connection(s) from the default table",
        len(reg.all()),
    )
    return reg

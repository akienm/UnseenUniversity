"""
connections.py — the model<->provider CONNECTIONS stack for the inference router.

STUB (proof scaffold, T-inference-connections-stack). The real implementation lands in
the next commit; this stub exists so proof_emitter sees a MODIFIED file (stub->real)
and gets an authentic AssertionError red instead of an ImportError from an added file.
See D-inference-router-stack-decomposition-2026-07-08.
"""

from __future__ import annotations

from dataclasses import dataclass

from unseen_university.devices.inference.models_registry import ModelsRegistry


@dataclass(frozen=True)
class Connection:
    model_id: str
    source_name: str
    dollars_per_unit: float = 0.0


class ConnectionsRegistry:
    def __init__(self) -> None:
        self._by_model: dict[str, list[Connection]] = {}

    def register(self, conn: Connection) -> None:
        raise NotImplementedError

    def by_model(self, model_id: str) -> list[Connection]:
        return []

    def all(self) -> list[Connection]:
        return []


def seed_from_models(models: ModelsRegistry) -> ConnectionsRegistry:
    return ConnectionsRegistry()

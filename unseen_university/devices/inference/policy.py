"""policy.py — STUB (proof scaffold, T-inference-rules-as-policy).

Stub so proof_emitter binds a MODIFIED file (stub->real) for an authentic AssertionError
red. Real impl next commit. See D-inference-router-stack-decomposition-2026-07-08.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from unseen_university.devices.inference.dimensions import RouteRequest


@dataclass(frozen=True)
class CapabilityEnvelope:
    min_difficulty: str
    required_domain: str = ""
    required_features: frozenset[str] = field(default_factory=frozenset)

    def tighten(self, **kwargs) -> "CapabilityEnvelope":
        return self


@dataclass(frozen=True)
class PolicyRule:
    label: str
    when: dict = field(default_factory=dict)
    min_difficulty: str = ""
    required_features: tuple = ()
    required_domain: str = ""

    def matches(self, req: RouteRequest) -> bool:
        return False


_DEFAULT_POLICIES: list = []


def build_envelope(req: RouteRequest, policies=None) -> CapabilityEnvelope:
    return CapabilityEnvelope(min_difficulty="classify")

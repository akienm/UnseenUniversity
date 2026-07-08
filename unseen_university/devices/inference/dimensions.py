"""dimensions.py — STUB (proof scaffold, T-inference-dimensions-contract).

Stub so proof_emitter binds a MODIFIED file (stub->real) for an authentic AssertionError
red. Real impl lands next commit. See D-inference-router-stack-decomposition-2026-07-08.
"""

from __future__ import annotations

from dataclasses import dataclass

ROLE_TIERS: tuple[str, ...] = ("apprentice", "builder", "creator", "master", "guru")


def difficulty_seed(ticket_tier: str) -> str:
    return "classify"


@dataclass(frozen=True)
class RouteRequest:
    ticket_tier: str
    builder_tier: str
    domain: str = ""
    urgency: str = "normal"
    escalation_allowed: bool = True

    @property
    def seed_difficulty(self) -> str:
        return difficulty_seed(self.ticket_tier)

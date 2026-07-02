"""
coding.py — CodingCapability: the capability that exposes the coding domain on an agent.

D-agent-capability-mixins-over-domains-2026-07-02 (stream A). The first capability
specialization. It carries no coding logic — it binds `capability_domain = "coding"` so
`run_capability` resolves and delegates to `CodingDomain` (via resolve_domain('coding')).
An agent gains coding by mixing this in; the coding behaviour stays wholly in the Domain.
"""

from __future__ import annotations

from unseen_university.capabilities.base import CapabilityMixin


class CodingCapability(CapabilityMixin):
    """Exposes the coding domain (CodingDomain) as a mixin-composable agent capability."""

    capability_domain = "coding"

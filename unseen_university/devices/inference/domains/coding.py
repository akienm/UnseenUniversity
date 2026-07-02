"""
coding.py — the coding task domain.

CodingDomain owns the coding domain's model selection + prompts. It is the first
registered Domain specialization (D-domain-object-encapsulation-2026-07-01). Selection
is inherited behavior-preserving from BaseDomain (delegating to the RulesEngine selector
with domain='coding'); the coding system prompt resolves from the domain-prompt store.
"""

from __future__ import annotations

from unseen_university.devices.inference.domains.base import BaseDomain


class CodingDomain(BaseDomain):
    """The coding domain — model selection + prompts for coding tasks."""

    name = "coding"

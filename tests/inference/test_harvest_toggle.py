"""harvest operator on-switch (T-ds-harvest-mode-operator-toggle).

resolve_domain reads UU_HARVEST_MODE at the single construction chokepoint: a harvest session
runs the DS process with the env set truthy, so every domain that process resolves is built with
the HARVEST_POLICY (the escalation walk then terminates at the fixed tier). Unset/falsey = the
DEFAULT_POLICY (production escalates).

PROOF NODE: with UU_HARVEST_MODE=1 the resolved coding domain carries HARVEST_POLICY. Red (a
resolver that ignores the env → DEFAULT_POLICY) → green.
"""
from __future__ import annotations

from unseen_university.devices.inference.domains import resolve_domain
from unseen_university.devices.inference.domains.escalation_policy import (
    DEFAULT_POLICY,
    HARVEST_POLICY,
)


def test_env_toggle_turns_on_harvest_policy(monkeypatch):
    """PROOF: UU_HARVEST_MODE=1 makes resolve_domain construct HARVEST_POLICY domains."""
    monkeypatch.setenv("UU_HARVEST_MODE", "1")
    assert resolve_domain("coding").escalation_policy is HARVEST_POLICY
    assert resolve_domain("").escalation_policy is HARVEST_POLICY  # generalist path too


def test_unset_env_defaults_off(monkeypatch):
    monkeypatch.delenv("UU_HARVEST_MODE", raising=False)
    assert resolve_domain("coding").escalation_policy is DEFAULT_POLICY


def test_falsey_env_stays_off(monkeypatch):
    monkeypatch.setenv("UU_HARVEST_MODE", "false")
    assert resolve_domain("coding").escalation_policy is DEFAULT_POLICY


def test_truthy_spellings_all_on(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "On"):
        monkeypatch.setenv("UU_HARVEST_MODE", v)
        assert resolve_domain("coding").escalation_policy is HARVEST_POLICY, f"{v!r} should be truthy"

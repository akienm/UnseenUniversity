"""harvest_mode operator on-switch (T-ds-harvest-mode-operator-toggle).

resolve_domain reads UU_HARVEST_MODE at the single construction chokepoint: a harvest session
runs the DS process with the env set truthy, so every domain that process resolves is built with
harvest_mode=True (the escalation walk then terminates at the fixed tier). Unset/falsey = OFF.

PROOF NODE: with UU_HARVEST_MODE=1 the resolved coding domain has harvest_mode True. Red (a
resolver that ignores the env → False) → green.
"""
from __future__ import annotations

from unseen_university.devices.inference.domains import resolve_domain


def test_env_toggle_turns_on_harvest_mode(monkeypatch):
    """PROOF: UU_HARVEST_MODE=1 makes resolve_domain construct harvest_mode=True domains."""
    monkeypatch.setenv("UU_HARVEST_MODE", "1")
    assert resolve_domain("coding").harvest_mode is True
    assert resolve_domain("").harvest_mode is True  # generalist path too


def test_unset_env_defaults_off(monkeypatch):
    monkeypatch.delenv("UU_HARVEST_MODE", raising=False)
    assert resolve_domain("coding").harvest_mode is False


def test_falsey_env_stays_off(monkeypatch):
    monkeypatch.setenv("UU_HARVEST_MODE", "false")
    assert resolve_domain("coding").harvest_mode is False


def test_truthy_spellings_all_on(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "On"):
        monkeypatch.setenv("UU_HARVEST_MODE", v)
        assert resolve_domain("coding").harvest_mode is True, f"{v!r} should be truthy"

"""
Tests for Granny exact_match mode.

Tests:
- match_rule exact_match=False: default fallback fires for unmatched ticket
- match_rule exact_match=True: default catch-all (no 'when') is skipped
- match_rule exact_match=True: returns None for ticket with no matching role rule
- match_rule exact_match=True: still matches specific role rules
- match_rule exact_match=True: tags_any rule still fires
- run_once exact_match=True: defers ticket with no matching role (logs warning, no dispatch)
- run_once exact_match=False: falls through to CC.0 for unmatched ticket
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from unseen_university.devices.granny.daemon import match_rule, run_once


_RULES = [
    {"when": {"tags_any": ["Security"]}, "route_to": "CC.0"},
    {"when": {"role_in": ["master"]}, "route_to": "CC.0"},
    {"when": {"role_in": ["builder", "creator"]}, "route_to": "DickSimnel.0"},
    {"route_to": "CC.0"},  # default catch-all
]


# ── match_rule unit tests ─────────────────────────────────────────────────────

def test_match_rule_default_fallback_fires_when_not_exact():
    """With exact_match=False, unmatched ticket falls through to default CC.0."""
    ticket = {"id": "T-x", "role": "apprentice", "tags": []}
    result = match_rule(ticket, _RULES, exact_match=False)
    assert result == "CC.0"


def test_match_rule_exact_match_skips_catch_all():
    """With exact_match=True, the default rule (no 'when') is skipped."""
    ticket = {"id": "T-x", "role": "apprentice", "tags": []}
    result = match_rule(ticket, _RULES, exact_match=True)
    assert result is None


def test_match_rule_exact_match_returns_none_for_no_match():
    """With exact_match=True and no matching rule, returns None."""
    ticket = {"id": "T-x", "role": "guru", "tags": []}
    rules_no_guru = [
        {"when": {"role_in": ["master"]}, "route_to": "CC.0"},
        {"when": {"role_in": ["builder"]}, "route_to": "DickSimnel.0"},
    ]
    result = match_rule(ticket, rules_no_guru, exact_match=True)
    assert result is None


def test_match_rule_exact_match_still_matches_role_rule():
    """With exact_match=True, a matching role rule still fires."""
    ticket = {"id": "T-x", "role": "master", "tags": []}
    result = match_rule(ticket, _RULES, exact_match=True)
    assert result == "CC.0"


def test_match_rule_exact_match_builder_routes_to_ds():
    """With exact_match=True, builder role routes to DickSimnel."""
    ticket = {"id": "T-x", "role": "builder", "tags": []}
    result = match_rule(ticket, _RULES, exact_match=True)
    assert result == "DickSimnel.0"


def test_match_rule_exact_match_tags_any_still_fires():
    """With exact_match=True, tags_any rules are not skipped."""
    ticket = {"id": "T-x", "role": "apprentice", "tags": ["Security"]}
    result = match_rule(ticket, _RULES, exact_match=True)
    assert result == "CC.0"


def test_match_rule_default_is_false():
    """match_rule defaults to exact_match=False (same as legacy behavior)."""
    ticket = {"id": "T-x", "role": "apprentice", "tags": []}
    # No exact_match kwarg — should behave like exact_match=False
    result = match_rule(ticket, _RULES)
    assert result == "CC.0"


# ── run_once exact_match integration tests ───────────────────────────────────

def _minimal_config(exact_match: bool) -> dict:
    return {
        "exact_match": exact_match,
        "granny_mailbox": "granny.0",
        "workers": {
            "CC.0": {"dispatch": "bus", "mailbox": "cc.0", "one_at_a_time": False},
        },
        "rules": [
            {"when": {"role_in": ["master"]}, "route_to": "CC.0"},
            {"route_to": "CC.0"},  # default catch-all
        ],
    }


def test_run_once_exact_match_defers_unmatched_ticket(caplog):
    """run_once with exact_match=True skips tickets with no matching role rule."""
    config = {
        "exact_match": True,
        "granny_mailbox": "granny.0",
        "workers": {
            "CC.0": {"dispatch": "bus", "mailbox": "cc.0", "one_at_a_time": False},
        },
        "rules": [
            {"when": {"role_in": ["master"]}, "route_to": "CC.0"},
            # no default catch-all
        ],
    }
    apprentice_ticket = {
        "id": "T-apprentice-1",
        "status": "sprint",
        "role": "apprentice",
        "tags": [],
        "title": "some apprentice task",
        "priority": 0.5,
    }

    with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[apprentice_ticket]), \
         patch("unseen_university.devices.granny.daemon._cleared_gated_tickets", return_value=[]), \
         patch("unseen_university.devices.granny.daemon._load_announced_workers", return_value={}), \
         patch("unseen_university.devices.granny.daemon._process_handshake_replies", return_value=0), \
         patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
         patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
         patch("unseen_university.devices.granny.daemon._post_channel"), \
         patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
         patch("unseen_university.devices.granny.availability.check_and_expire_cooldowns"), \
         patch("unseen_university.devices.granny.daemon._dispatch_bus") as mock_dispatch:
        import logging
        with caplog.at_level(logging.WARNING, logger="unseen_university.devices.granny.daemon"):
            run_once(config, imap=MagicMock())

    mock_dispatch.assert_not_called()
    assert "exact_match_defer" in caplog.text
    assert "T-apprentice-1" in caplog.text


def test_run_once_exact_match_false_dispatches_unmatched_to_cc():
    """run_once with exact_match=False still dispatches unmatched tickets via default CC.0 rule."""
    config = _minimal_config(exact_match=False)
    ticket = {
        "id": "T-no-role-1",
        "status": "sprint",
        "role": "apprentice",
        "tags": [],
        "title": "task without explicit route",
        "priority": 0.5,
    }

    with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
         patch("unseen_university.devices.granny.daemon._cleared_gated_tickets", return_value=[]), \
         patch("unseen_university.devices.granny.daemon._load_announced_workers", return_value={}), \
         patch("unseen_university.devices.granny.daemon._process_handshake_replies", return_value=0), \
         patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
         patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
         patch("unseen_university.devices.granny.daemon._post_channel"), \
         patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
         patch("unseen_university.devices.granny.availability.check_and_expire_cooldowns"), \
         patch("unseen_university.devices.granny.daemon._dispatch_bus", return_value=True) as mock_dispatch:
        run_once(config, imap=MagicMock())

    mock_dispatch.assert_called_once()

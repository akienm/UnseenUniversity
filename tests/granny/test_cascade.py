"""
Tests for Granny cascade tier pickup.

Tests:
- _cascade_active_workers: no cascade when own-tier tickets exist
- _cascade_active_workers: cascade active when own-tier empty
- _cascade_active_workers: skips workers without cascade_if_idle flag
- _cascade_active_workers: cascade_if_idle=false → no cascade
- run_once cascade routing: builder ticket → CC.0 when cascade active
- run_once cascade routing: no cascade when CC.0 has master tickets
- cascade worker field update: _setstatus_direct called with worker=claude
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from devices.granny.daemon import (
    _TIER_ORDER,
    _cascade_active_workers,
    _infer_role,
    match_rule,
    run_once,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfg(cascade: bool = True) -> dict:
    """Minimal granny config with CC.0 having cascade_if_idle set."""
    return {
        "workers": {
            "CC.0": {
                "dispatch": "bus",
                "mailbox": "cc.0",
                "one_at_a_time": True,
                "cascade_if_idle": cascade,
            },
            "DickSimnel.0": {
                "dispatch": "bus",
                "mailbox": "dicksimnel.0",
            },
        },
        "rules": [
            {"when": {"role_in": ["master"]}, "route_to": "CC.0"},
            {"when": {"role_in": ["builder", "creator"]}, "route_to": "DickSimnel.0"},
            {"route_to": "CC.0"},
        ],
    }


def _ticket(role: str, tid: str = "T-test") -> dict:
    return {"id": tid, "role": role, "tags": [], "status": "sprint", "title": tid}


# ── _cascade_active_workers ───────────────────────────────────────────────────

def test_cascade_active_when_master_queue_empty():
    cfg = _cfg(cascade=True)
    tickets = [_ticket("builder")]  # only builder tickets — CC.0's master queue is empty
    result = _cascade_active_workers(cfg, tickets)
    assert "CC.0" in result
    # CC.0 should absorb creator/builder/apprentice
    assert "creator" in result["CC.0"]
    assert "builder" in result["CC.0"]


def test_cascade_inactive_when_master_queue_nonempty():
    cfg = _cfg(cascade=True)
    tickets = [_ticket("master"), _ticket("builder")]  # master ticket exists
    result = _cascade_active_workers(cfg, tickets)
    assert "CC.0" not in result  # own-tier has work — no cascade


def test_cascade_skips_workers_without_flag():
    cfg = _cfg(cascade=False)  # cascade_if_idle=False
    tickets = [_ticket("builder")]
    result = _cascade_active_workers(cfg, tickets)
    assert "CC.0" not in result


def test_cascade_skips_workers_with_no_flag():
    """Workers without cascade_if_idle key at all are skipped."""
    cfg = {
        "workers": {
            "CC.0": {"dispatch": "bus", "mailbox": "cc.0"},  # no cascade_if_idle
        },
        "rules": [{"when": {"role_in": ["master"]}, "route_to": "CC.0"}],
    }
    result = _cascade_active_workers(cfg, [_ticket("builder")])
    assert "CC.0" not in result


def test_cascade_empty_ticket_list():
    """No tickets at all → cascade active (own tier definitely empty)."""
    cfg = _cfg(cascade=True)
    result = _cascade_active_workers(cfg, [])
    assert "CC.0" in result


def test_tier_order_is_descending():
    """_TIER_ORDER goes from highest authority to lowest."""
    assert _TIER_ORDER.index("master") < _TIER_ORDER.index("builder")
    assert _TIER_ORDER.index("builder") < _TIER_ORDER.index("apprentice")


# ── run_once cascade routing ──────────────────────────────────────────────────

def _run_once_with_mocks(tickets: list[dict], cascade: bool = True):
    """
    Run one run_once cycle with mock DB/IMAP/availability.
    Returns list of (ticket_id, target_mailbox) pairs that were dispatched.
    """
    cfg = _cfg(cascade=cascade)
    dispatched = []
    mock_imap = MagicMock()

    def fake_dispatch_bus(ticket, imap_, mailbox, granny_mailbox):
        dispatched.append((ticket["id"], mailbox))
        return True

    with patch("devices.granny.daemon._sprint_tickets", return_value=tickets), \
         patch("devices.granny.availability.is_available", return_value=True), \
         patch("devices.granny.daemon._cc0_busy", return_value=False), \
         patch("devices.granny.daemon._dispatch_bus", side_effect=fake_dispatch_bus), \
         patch("devices.granny.daemon._dispatch_akien", return_value=True), \
         patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
         patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
         patch("devices.granny.daemon._process_handshake_replies", return_value=0), \
         patch("devices.granny.daemon._post_channel"), \
         patch("devices.granny.daemon._setstatus_direct", return_value=True), \
         patch("devices.granny.availability.check_and_expire_cooldowns"), \
         patch("devices.granny.daemon._CIRCUIT_STATE_FILE",
               Path("/tmp/nonexistent_circuit_state.json")), \
         patch("devices.granny.workflow_executor.get_executor") as mock_executor:
        mock_executor.return_value.tick.return_value = None
        run_once(cfg, imap=mock_imap)

    return dispatched


def test_builder_ticket_routes_to_cc_when_cascade_active():
    """Builder ticket → CC.0 (cc.0 mailbox) when no master tickets exist (cascade)."""
    tickets = [_ticket("builder", "T-builder")]
    dispatched = _run_once_with_mocks(tickets, cascade=True)
    assert len(dispatched) == 1
    tid, mailbox = dispatched[0]
    assert tid == "T-builder"
    assert mailbox == "cc.0"  # CC.0's mailbox


def test_builder_ticket_routes_to_ds_when_no_cascade():
    """Builder ticket → DickSimnel.0 (dicksimnel.0 mailbox) when cascade=False."""
    tickets = [_ticket("builder", "T-builder")]
    dispatched = _run_once_with_mocks(tickets, cascade=False)
    assert len(dispatched) == 1
    tid, mailbox = dispatched[0]
    assert tid == "T-builder"
    assert mailbox == "dicksimnel.0"


def test_builder_ticket_stays_with_ds_when_master_exists():
    """When master ticket exists, CC.0 cascade is inactive — builder goes to DickSimnel."""
    tickets = [_ticket("master", "T-master"), _ticket("builder", "T-builder")]
    dispatched = _run_once_with_mocks(tickets, cascade=True)
    # Both should be dispatched: master → CC.0, builder → DickSimnel.0
    assert len(dispatched) == 2
    mailboxes = {tid: mb for tid, mb in dispatched}
    assert mailboxes.get("T-master") == "cc.0"
    assert mailboxes.get("T-builder") == "dicksimnel.0"


def test_cascade_worker_field_updated():
    """When cascade fires, _setstatus_direct is called with worker='claude'."""
    cfg = _cfg(cascade=True)
    tickets = [_ticket("builder", "T-cascade")]
    mock_imap = MagicMock()
    status_calls = []

    def fake_setstatus(tid, status, worker=None):
        status_calls.append((tid, status, worker))
        return True

    with patch("devices.granny.daemon._sprint_tickets", return_value=tickets), \
         patch("devices.granny.availability.is_available", return_value=True), \
         patch("devices.granny.daemon._cc0_busy", return_value=False), \
         patch("devices.granny.daemon._dispatch_bus", return_value=True), \
         patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
         patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
         patch("devices.granny.daemon._process_handshake_replies", return_value=0), \
         patch("devices.granny.daemon._post_channel"), \
         patch("devices.granny.daemon._setstatus_direct", side_effect=fake_setstatus), \
         patch("devices.granny.availability.check_and_expire_cooldowns"), \
         patch("devices.granny.daemon._CIRCUIT_STATE_FILE",
               Path("/tmp/nonexistent_circuit_state.json")), \
         patch("devices.granny.workflow_executor.get_executor") as mock_executor:
        mock_executor.return_value.tick.return_value = None
        run_once(cfg, imap=mock_imap)

    # Verify that after cascade dispatch, worker was updated to 'claude'
    cascade_worker_updates = [
        c for c in status_calls if c[0] == "T-cascade" and c[2] == "claude"
    ]
    assert len(cascade_worker_updates) >= 1, (
        f"Expected setstatus_direct with worker='claude' for T-cascade, got: {status_calls}"
    )

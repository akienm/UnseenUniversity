"""
tests/granny/test_e2e_dispatch_smoke.py — Granny→CC dispatch chain smoke test.

Covers the full dispatch cycle without a live IMAP server or tmux session:
  sprint ticket → run_once() → dispatch envelope in cc.0
  dispatch_ack reply → run_once() → ticket transitions to acked
  dispatch_started reply → run_once() → ticket transitions to in_progress
  dispatch_timeout reply → run_once() → ticket transitions to escalated

Mocks: Postgres queue helpers (_sprint_tickets, _setstatus_direct), availability
flag, stale-ticket watchdogs, channel posts.

Real: run_once() logic, match_rule(), _dispatch_bus() envelope construction,
_process_handshake_replies() state machine.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ── Stubs ─────────────────────────────────────────────────────────────────────


class _StubIMAP:
    """In-process IMAP stub for bus dispatch tests."""

    def __init__(self):
        self._mailboxes: dict[str, list] = {}

    def append(self, mailbox: str, envelope) -> None:
        self._mailboxes.setdefault(mailbox, []).append(envelope)

    def fetch_unseen(self, mailbox: str) -> list:
        return self._mailboxes.pop(mailbox, [])

    def envelopes_in(self, mailbox: str) -> list:
        return list(self._mailboxes.get(mailbox, []))

    def inject_reply(self, mailbox: str, envelope) -> None:
        self._mailboxes.setdefault(mailbox, []).append(envelope)


def _ticket(ticket_id: str, role: str = "master", tags: list | None = None) -> dict:
    return {
        "id": ticket_id,
        "title": f"Test {ticket_id}",
        "status": "sprint",
        "role": role,
        "worker": "claude",
        "tags": tags or [],
        "priority": 0.5,
    }


def _reply(kind: str, ticket_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        from_device="cc.0",
        to_device="granny.0",
        payload={"kind": kind, "ticket_id": ticket_id, "from_device": "cc.0"},
    )


_CONFIG = {
    "workers": {
        "CC.0": {"dispatch": "bus", "mailbox": "cc.0", "one_at_a_time": True},
    },
    "rules": [{"route_to": "CC.0"}],
    "granny_mailbox": "granny.0",
}

_PATCHES = dict(
    sprint_tickets="devices.granny.daemon._sprint_tickets",
    setstatus="devices.granny.daemon._setstatus_direct",
    cc0_busy="devices.granny.daemon._cc0_busy",
    escalate="devices.granny.daemon._escalate_stale_dispatched",
    reset_stale="devices.granny.daemon._reset_stale_inprogress",
    available="devices.granny.availability.is_available",
    channel="devices.granny.daemon._post_channel",
)


def _run(imap, tickets, replies=None, available=True):
    """Run one poll cycle and return captured status transitions."""
    if replies:
        for r in replies:
            imap.inject_reply("granny.0", r)
    transitions = []

    with (
        patch(_PATCHES["sprint_tickets"], return_value=tickets),
        patch(_PATCHES["setstatus"], side_effect=lambda *a, **kw: transitions.append(a[:2]) or True),
        patch(_PATCHES["cc0_busy"], return_value=False),
        patch(_PATCHES["escalate"]),
        patch(_PATCHES["reset_stale"]),
        patch(_PATCHES["available"], return_value=available),
        patch(_PATCHES["channel"]),
    ):
        from devices.granny.daemon import run_once
        run_once(_CONFIG, imap=imap)

    return transitions


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestDispatchEnvelope:
    def test_sprint_ticket_sends_dispatch_to_cc0(self):
        """Granny puts a dispatch envelope in cc.0 for a sprint ticket."""
        imap = _StubIMAP()
        _run(imap, [_ticket("T-e2e-001")])

        envelopes = imap.envelopes_in("cc.0")
        assert len(envelopes) == 1
        env = envelopes[0]
        assert env.payload["kind"] == "dispatch"
        assert env.payload["ticket_id"] == "T-e2e-001"

    def test_dispatch_envelope_addressing(self):
        """Envelope has from_device=granny.0, to_device=cc.0."""
        imap = _StubIMAP()
        _run(imap, [_ticket("T-e2e-002")])

        env = imap.envelopes_in("cc.0")[0]
        assert env.from_device == "granny.0"
        assert env.to_device == "cc.0"

    def test_dispatch_records_dispatched_status(self):
        """_dispatch_bus marks the ticket as dispatched."""
        imap = _StubIMAP()
        transitions = _run(imap, [_ticket("T-e2e-003")])
        assert ("T-e2e-003", "dispatched") in transitions


class TestHandshakeReplies:
    def test_dispatch_ack_transitions_to_acked(self):
        """dispatch_ack reply → ticket transitions to acked."""
        imap = _StubIMAP()
        transitions = _run(imap, [], replies=[_reply("dispatch_ack", "T-e2e-004")])
        assert ("T-e2e-004", "acked") in transitions

    def test_dispatch_started_transitions_to_in_progress(self):
        """dispatch_started reply → ticket transitions to in_progress."""
        imap = _StubIMAP()
        transitions = _run(imap, [], replies=[_reply("dispatch_started", "T-e2e-005")])
        assert ("T-e2e-005", "in_progress") in transitions

    def test_dispatch_timeout_transitions_to_escalated(self):
        """dispatch_timeout reply → ticket transitions to escalated."""
        imap = _StubIMAP()
        transitions = _run(imap, [], replies=[_reply("dispatch_timeout", "T-e2e-006")])
        assert ("T-e2e-006", "escalated") in transitions


class TestDispatchGates:
    def test_cc0_unavailable_defers_ticket(self):
        """No envelope sent when CC.0 is unavailable."""
        imap = _StubIMAP()
        _run(imap, [_ticket("T-e2e-007")], available=False)
        assert imap.envelopes_in("cc.0") == []

    def test_one_at_a_time_blocks_second_ticket(self):
        """Only one ticket dispatched per cycle when one_at_a_time=True."""
        imap = _StubIMAP()
        transitions = _run(imap, [_ticket("T-e2e-008a"), _ticket("T-e2e-008b")])

        dispatched = [tid for tid, st in transitions if st == "dispatched"]
        assert len(dispatched) == 1

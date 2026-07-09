"""Cost-ordered stuck-ladder controller + rung-choice record (T-ds-stuck-ladder-and-rung-log).

The ladder is the structured exit for a harvest_mode wall: walk the four cost-ordered rungs
(answer / drop-ticket / halt / call-CC) cheapest-first, actuate + record the first viable one.
The distribution over rungs is the builder starve-curve; call-CC frequency is the starved-
resource metric. These tests drive the ladder hermetically (injected ``root`` → tmp store,
injected hooks) — no real device, no real memory store.

PROOF NODE: a stuck event with no compiled answer and HALT unavailable routes to CALL-CC AND
emits exactly one rung-choice record carrying the real rung + tier + turn_reached. Red (a ladder
that selects but never records) → green.
"""
from __future__ import annotations

from unseen_university.devices.inference.domains.stuck_ladder import (
    RUNG_ANSWER,
    RUNG_CALL_CC,
    RUNG_DROP_TICKET,
    RUNG_HALT,
    StuckEvent,
    StuckLadder,
    read_rung_choices,
)

_EVENT = StuckEvent(ticket_id="T-stuck-proof", tier="code", turn_reached=7, domain="coding")


def test_no_answer_no_halt_routes_to_call_cc_and_records(tmp_path):
    """PROOF: no compiled answer + HALT unavailable → CALL-CC, and ONE record with real fields.

    A ladder that picks call-CC but records nothing (the naive form) fails the record read-back.
    """
    fired: list[str] = []
    ladder = StuckLadder(
        call_cc_hook=lambda ev: fired.append(ev.ticket_id),  # rung-4 actuation seam
        root=tmp_path,  # redirect the record store off the real memory root
    )

    choice = ladder.resolve(_EVENT)

    assert choice.rung == RUNG_CALL_CC
    assert fired == ["T-stuck-proof"], "the call-CC actuation seam must fire when rung 4 is taken"

    records = read_rung_choices(root=tmp_path)
    assert len(records) == 1, f"exactly one rung-choice record expected, got {len(records)}"
    rec = records[0]
    assert rec["rung"] == RUNG_CALL_CC
    assert rec["tier"] == "code" and rec["turn_reached"] == 7  # real wall fields, not hollow zeros
    assert rec["ticket_id"] == "T-stuck-proof" and rec["domain"] == "coding"


def test_answer_available_takes_free_rung_first(tmp_path):
    """Rung 1 (ANSWER) is cheapest — a non-None answer_source wins before any other rung."""
    ladder = StuckLadder(answer_source=lambda ev: "compiled answer", root=tmp_path)
    assert ladder.resolve(_EVENT).rung == RUNG_ANSWER


def test_design_stuck_takes_drop_ticket_before_halt_and_cc(tmp_path):
    """Rung 2 (DROP-TICKET) beats halt/call-CC when the classifier says design-stuck."""
    ladder = StuckLadder(
        drop_classifier=lambda ev: True,
        halt_hook=lambda ev: None,  # even with HALT available, drop is cheaper
        root=tmp_path,
    )
    assert ladder.resolve(_EVENT).rung == RUNG_DROP_TICKET


def test_halt_taken_when_available_before_call_cc(tmp_path):
    """Rung 3 (HALT) beats call-CC when the HALT seam is wired, and its hook fires."""
    halted: list[str] = []
    ladder = StuckLadder(halt_hook=lambda ev: halted.append(ev.ticket_id), root=tmp_path)
    choice = ladder.resolve(_EVENT)
    assert choice.rung == RUNG_HALT
    assert halted == ["T-stuck-proof"]


def test_call_cc_frequency_is_countable(tmp_path):
    """The starved-resource metric: call-CC frequency = count of call_cc records."""
    ladder = StuckLadder(root=tmp_path)
    ladder.resolve(StuckEvent(ticket_id="T-a", tier="code", turn_reached=1))
    ladder.resolve(StuckEvent(ticket_id="T-b", tier="code", turn_reached=2))
    call_cc = [r for r in read_rung_choices(root=tmp_path) if r["rung"] == RUNG_CALL_CC]
    assert len(call_cc) == 2

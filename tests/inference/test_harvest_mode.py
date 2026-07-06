"""Harvest-mode escalation-disable proof (T-ds-harvest-mode-escalation-off).

The coding loop's escalation WALK lives in BaseDomain.run: a CAPABILITY failure (a terminal
that never reached DONE) bumps difficulty one rung and re-dispatches at a pricier tier. For
the failure-harvesting testing phase that confounds the builder starve-curve — escalating
mid-run mixes 'the cheap builder couldn't' with 'a stronger one could'. harvest_mode gates
that bump: when on, the walk terminates at the fixed tier so the wall itself is the signal.

These tests drive the walk directly through a recording BaseDomain subclass whose
`_run_attempt` records the escalation_hop it was asked to run at and returns a capability-fail
LoopResult — no real inference, fully hermetic. `system_alarms.raise_alarm` is patched so the
control test's ceiling walk (which fires an alarm at the top rung) stays offline.

PROOF NODE: with harvest_mode on, a capability failure yields exactly ONE attempt at hop 0
(the escalation counter never increments) and a terminal None. Red (today's walk escalates to
the ceiling: hops_seen == [0, 1, ...]) → green (hops_seen == [0]).
"""
from __future__ import annotations

from unittest.mock import patch

from unseen_university.devices.inference.agentic_loop import LOOP_ESCALATE, LoopResult
from unseen_university.devices.inference.domains.base import BaseDomain, DomainPrompts

_TICKET = {"id": "T-harvest-proof", "title": "always-fails", "tags": [], "description": "d"}


class _RecordingDomain(BaseDomain):
    """A generalist domain whose every attempt is a capability wall, recording each hop.

    Overrides only `_run_attempt` (what one attempt IS) and `prompts` (to stay off the
    domain-prompt store) — the escalation walk in BaseDomain.run is exactly the code under test.
    """

    def __init__(self, *, harvest_mode: bool = False) -> None:
        super().__init__(name="", harvest_mode=harvest_mode)
        self.hops_seen: list[int] = []

    @property
    def prompts(self) -> DomainPrompts:
        return DomainPrompts(system="test system")

    def _run_attempt(self, *, system_prompt, ticket, ticket_id, agent_id,
                     escalation_hop, prior_attempt) -> LoopResult:
        self.hops_seen.append(escalation_hop)
        return LoopResult(LOOP_ESCALATE, text="could not finish")  # → classifies as 'capability'


def test_harvest_mode_terminates_walk_at_first_capability_wall():
    """PROOF: harvest_mode disables the escalation walk — one attempt at hop 0, then terminate.

    Pre-fix the walk bumps 0→1→…→ceiling (hops_seen len > 1); the flag must short-circuit the
    capability branch so hops_seen == [0] and the run returns None with escalation_hop still 0.
    """
    d = _RecordingDomain(harvest_mode=True)
    with patch("unseen_university.system_alarms.raise_alarm") as alarm:
        result = d.run(_TICKET)

    assert d.hops_seen == [0], (
        f"harvest_mode must NOT escalate: expected a single attempt at hop 0, got {d.hops_seen}"
    )
    assert result is None
    alarm.assert_not_called()  # a harvested wall is the wanted outcome, never an incident


def test_harvest_mode_off_walks_the_escalation_ladder():
    """Control: production (harvest_mode=False) still escalates capability failures to the ceiling."""
    d = _RecordingDomain(harvest_mode=False)
    with patch("unseen_university.system_alarms.raise_alarm"):
        result = d.run(_TICKET)

    assert len(d.hops_seen) > 1, "default behavior must still walk the escalation ladder"
    assert d.hops_seen[0] == 0 and d.hops_seen[1] == 1
    assert result is None  # ceiling reached (alarm patched)


def test_harvest_mode_logs_escalation_disabled_at_entry(caplog):
    """The mode is observable: an INFO line at loop entry names harvest_mode + escalation disabled."""
    import logging

    d = _RecordingDomain(harvest_mode=True)
    with caplog.at_level(logging.INFO, logger="unseen_university.devices.inference.domains.base"), \
         patch("unseen_university.system_alarms.raise_alarm"):
        d.run(_TICKET)

    assert any(
        "harvest_mode=on" in r.getMessage() and "escalation disabled" in r.getMessage()
        for r in caplog.records
    ), f"expected harvest_mode entry log, got: {[r.getMessage() for r in caplog.records]}"

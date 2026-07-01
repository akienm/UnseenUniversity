"""Tests for the DickSimnel escalation driver — the ONE difficulty walk.

The old builder→creator→CC _TIER_CASCADE is retired (T-router-failure-bump-escalation):
DS._run_inference now walks DIFFICULTY up one rung per CAPABILITY failure, re-selects at the
SAME difficulty on an AVAILABILITY failure (never bumps to paid on a source-down), and halts
cleanly past the top rung. The two money-safety properties — availability→no-bump and
past-top→clean-halt-no-loop — are the load-bearing tests here.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.inference.rules_engine import _DEFAULT_RULES, RoutingRule


# ── 1. rules_engine has creator-tier rules ────────────────────────────────────


class TestCreatorTierRulesExist:
    def test_creator_rules_present(self):
        creator_rules = [r for r in _DEFAULT_RULES if r.task_class == "creator"]
        assert len(creator_rules) >= 1, "No creator-tier rules found in _DEFAULT_RULES"

    def test_creator_primary_rule_label(self):
        creator_rules = [r for r in _DEFAULT_RULES if r.task_class == "creator"]
        labels = [r.label for r in creator_rules]
        assert any("creator" in lbl for lbl in labels)

    def test_creator_rules_have_openrouter_source(self):
        creator_rules = [r for r in _DEFAULT_RULES if r.task_class == "creator"]
        sources = {r.source_name for r in creator_rules}
        assert "openrouter" in sources

    def test_creator_rule_priorities_are_ordered(self):
        creator_rules = sorted(
            [r for r in _DEFAULT_RULES if r.task_class == "creator"],
            key=lambda r: r.priority,
        )
        # Primary rule must have lower priority number than fallback
        assert creator_rules[0].priority < creator_rules[-1].priority

    def test_creator_tier_distinct_from_worker(self):
        creator_model_ids = {r.model_id for r in _DEFAULT_RULES if r.task_class == "creator"}
        worker_model_ids = {r.model_id for r in _DEFAULT_RULES if r.task_class == "worker"}
        # At least one creator model should differ from worker models
        assert creator_model_ids, "No creator rules found"
        # They should not be identical sets (creator exists as its own tier)
        assert creator_model_ids != worker_model_ids


# ── 2. The difficulty walk (T-router-failure-bump-escalation) ─────────────────


def _make_device():
    from unseen_university.devices.dicksimnel.device import DickSimnelDevice
    dev = DickSimnelDevice.__new__(DickSimnelDevice)
    dev._active_ticket = None
    return dev


def _drive(ticket, run_side_effect):
    """Run _run_inference with ToolLoop patched. Returns (result, hops, alarms).

    `run_side_effect(escalation_hop, prior_attempt, call_index) -> str|None` produces each
    attempt's ToolLoop.run return. `hops` is the escalation_hop passed to each attempt (the
    walk's spine); `alarms` is the list of raised system_alarm signatures.
    """
    hops: list[int] = []
    alarms: list[str] = []
    idx = {"n": 0}

    def fake_run(t, sp, escalation_hop=0, prior_attempt=""):
        hops.append(escalation_hop)
        out = run_side_effect(escalation_hop, prior_attempt, idx["n"])
        idx["n"] += 1
        return out

    loop_mock = MagicMock()
    loop_mock.run.side_effect = fake_run
    loop_mock._turn_log = []

    def fake_alarm(*, signature, caller, message, fatal=False):
        alarms.append(signature)

    with patch("unseen_university.devices.dicksimnel.device.DickSimnelDevice._build_system_prompt", return_value="sys"), \
         patch("unseen_university.devices.dicksimnel.toolloop.ToolLoop") as MockLoop, \
         patch("unseen_university.system_alarms.raise_alarm", side_effect=fake_alarm):
        MockLoop.return_value = loop_mock
        dev = _make_device()
        result = dev._run_inference(ticket)
    return result, hops, alarms


class TestDifficultyWalk:
    def test_capability_failure_bumps_difficulty_with_hop(self):
        """Case 1: a CAPABILITY failure (MAX_TURNS) re-dispatches at the next difficulty (hop+1)."""
        ticket = {"id": "T-cap", "description": "d", "tags": []}
        # hop 0 (code): capability fail; hop 1 (design): DONE.
        def se(hop, prior, i):
            if hop == 0:
                return '{"status": "error", "result": "MAX_TURNS: 20 turns"}'
            return "DONE: solved at the harder tier"
        result, hops, alarms = _drive(ticket, se)
        assert hops == [0, 1], f"capability fail must bump the hop: {hops}"
        assert result == "DONE: solved at the harder tier"
        assert alarms == []

    def test_success_first_pick_does_not_escalate(self):
        """Case 4: a DONE on the first pick does not escalate — one attempt, hop 0."""
        ticket = {"id": "T-ok", "description": "d", "tags": []}
        result, hops, alarms = _drive(ticket, lambda hop, prior, i: "DONE: nailed it")
        assert hops == [0]
        assert result == "DONE: nailed it"
        assert alarms == []

    # ── money-safety property 1: availability must NOT bump to a pricier tier ──
    def test_availability_failure_does_not_bump_difficulty(self):
        """Case 2 (MONEY SAFETY): an AVAILABILITY failure (None) re-selects at the SAME difficulty
        — the hop does NOT increment, so the walk never escalates to a paid tier on a source-down."""
        ticket = {"id": "T-avail", "description": "d", "tags": []}
        # first attempt: no live source (None); retry: DONE.
        def se(hop, prior, i):
            return None if i == 0 else "DONE: source came back"
        result, hops, alarms = _drive(ticket, se)
        assert hops == [0, 0], f"availability fail must NOT bump difficulty: {hops}"
        assert result == "DONE: source came back"
        assert alarms == []

    def test_availability_exhausted_halts_no_infinite_loop(self):
        """A PERSISTENT availability failure is bounded — it halts with an alarm, never loops."""
        ticket = {"id": "T-avail-dead", "description": "d", "tags": []}
        result, hops, alarms = _drive(ticket, lambda hop, prior, i: None)
        # _MAX_AVAILABILITY_RETRIES=2 → 1 initial + 2 retries = 3 attempts, all at hop 0, then halt.
        assert hops == [0, 0, 0], f"availability retries must be bounded at same difficulty: {hops}"
        assert result is None
        assert any("availability-exhausted" in a for a in alarms)

    # ── money-safety property 2: past-top is a clean halt, never a loop ──
    def test_past_top_tier_halts_with_alarm_no_loop(self):
        """Case 3 (MONEY SAFETY): repeated CAPABILITY failure bumps code→design then HALTS past the
        top rung — a clean system_alarm, NO loop (exactly 2 dispatches, hops 0 and 1)."""
        ticket = {"id": "T-ceiling", "description": "d", "tags": []}
        result, hops, alarms = _drive(ticket, lambda hop, prior, i: "MAX_TURNS: never finishes")
        assert hops == [0, 1], f"must attempt code then design then halt (no loop): {hops}"
        assert result is None
        assert any("capability-ceiling" in a for a in alarms)

    def test_cost_exceeded_halts_without_bumping(self):
        """COST_EXCEEDED halts (bumping to a pricier tier would only cost more) — one attempt, alarm."""
        ticket = {"id": "T-cost", "description": "d", "tags": []}
        result, hops, alarms = _drive(ticket, lambda hop, prior, i: "COST_EXCEEDED: $0.50 of $0.25 cap")
        assert hops == [0]
        assert result is None
        assert any("cost-cap" in a for a in alarms)

    def test_prior_attempt_threaded_on_capability_bump(self):
        """The prior attempt is threaded into the next (bumped) dispatch for handoff context."""
        ticket = {"id": "T-prior", "description": "d", "tags": []}
        seen_prior = {}
        def se(hop, prior, i):
            seen_prior[hop] = prior
            if hop == 0:
                return "ESCALATE: I could not finish this"
            return "DONE: done"
        _drive(ticket, se)
        assert seen_prior[0] == ""  # first attempt has no prior
        assert "could not finish" in seen_prior[1]  # bumped attempt carries the prior


class TestOneMechanismOnly:
    def test_tier_cascade_attribute_removed(self):
        """Case 5: the old parallel _TIER_CASCADE mechanism is gone — one driver only."""
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice
        assert not hasattr(DickSimnelDevice, "_TIER_CASCADE")

"""Tests for the escalation walk — the ONE difficulty walk, now OWNED by the domain.

D-domain-object-encapsulation: the walk moved out of DS._run_inference into
CodingDomain.run() (the single escalation owner), driving the shared AgenticLoop. DS is a
thin consumer that delegates to it. These tests drive CodingDomain.run() with the shared
loop mocked and assert the money-safety properties are preserved verbatim:
capability→bump (spends up), availability→NO-bump (re-select same difficulty),
past-top→clean-halt-no-loop, cost→halt. Plus: DS delegates to the domain, and the old
per-device loop mechanism is gone (one mechanism only).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from unseen_university.devices.inference.agentic_loop import (
    LOOP_AVAILABILITY,
    LOOP_COST_EXCEEDED,
    LOOP_DONE,
    LOOP_ESCALATE,
    LoopResult,
)
from unseen_university.devices.inference.rules_engine import _DEFAULT_RULES


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
        assert creator_rules[0].priority < creator_rules[-1].priority

    def test_creator_tier_distinct_from_worker(self):
        creator_model_ids = {r.model_id for r in _DEFAULT_RULES if r.task_class == "creator"}
        worker_model_ids = {r.model_id for r in _DEFAULT_RULES if r.task_class == "worker"}
        assert creator_model_ids
        assert creator_model_ids != worker_model_ids


# ── 2. The difficulty walk (now CodingDomain.run) ─────────────────────────────


def _to_loop_result(out: str | None) -> LoopResult:
    """Translate a test's str|None intent into the typed LoopResult the walk now reads.

    None → availability; COST_EXCEEDED: → cost; DONE:/done envelope → done; everything else
    (ESCALATE, MAX_TURNS, prose) → capability (finished-but-not-done). Same classification
    the old string-sniffing _classify_toolloop_result applied, now typed at the boundary.
    """
    if out is None:
        return LoopResult(LOOP_AVAILABILITY)
    s = out.strip()
    if s.startswith("COST_EXCEEDED"):
        return LoopResult(LOOP_COST_EXCEEDED, text=out)
    if s.startswith("DONE:"):
        return LoopResult(LOOP_DONE, text=out, envelope={"status": "done", "result": s[5:].strip()})
    return LoopResult(LOOP_ESCALATE, text=out)


def _drive(ticket, run_side_effect):
    """Run CodingDomain.run() with the shared AgenticLoop patched. Returns (result, hops, alarms).

    `run_side_effect(escalation_hop, prior_attempt, call_index) -> str|None` produces each
    attempt's outcome (translated to a LoopResult). `hops` is the escalation_hop the domain
    passes to each loop attempt (the walk's spine); `alarms` is the raised alarm signatures.
    """
    from unseen_university.devices.inference.domains.coding import CodingDomain

    hops: list[int] = []
    alarms: list[str] = []
    idx = {"n": 0}

    def fake_run(*, escalation_hop=0, prior_attempt="", **kw):
        hops.append(escalation_hop)
        out = run_side_effect(escalation_hop, prior_attempt, idx["n"])
        idx["n"] += 1
        return _to_loop_result(out)

    loop_mock = MagicMock()
    loop_mock.run.side_effect = fake_run

    def fake_alarm(*, signature, caller, message, fatal=False):
        alarms.append(signature)

    with patch("unseen_university.devices.inference.domains.base.AgenticLoop") as MockLoop, \
         patch("unseen_university.system_alarms.raise_alarm", side_effect=fake_alarm), \
         patch("unseen_university.devices.inference.domains.coding._orientation_prefix", return_value=""), \
         patch("unseen_university.devices.inference.domains.base.domain_prompt", return_value="sys"):
        MockLoop.return_value = loop_mock
        result = CodingDomain().run(ticket)
    return result, hops, alarms


class TestDifficultyWalk:
    def test_capability_failure_bumps_difficulty_with_hop(self):
        """Case 1: a CAPABILITY failure (MAX_TURNS) re-dispatches at the next difficulty (hop+1)."""
        ticket = {"id": "T-cap", "description": "d", "tags": []}
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
        """Case 2 (MONEY SAFETY): an AVAILABILITY failure re-selects at the SAME difficulty —
        the hop does NOT increment, so the walk never escalates to a paid tier on a source-down."""
        ticket = {"id": "T-avail", "description": "d", "tags": []}
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
        # max_availability_retries=2 → 1 initial + 2 retries = 3 attempts, all hop 0, then halt.
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

    def test_ds_classify_walk_removed(self):
        """DS no longer holds the walk/classifier — the domain owns it now."""
        import unseen_university.devices.dicksimnel.device as ds
        assert not hasattr(ds, "_classify_toolloop_result")
        assert not hasattr(ds, "_MAX_AVAILABILITY_RETRIES")

    def test_ds_toolloop_module_deleted(self):
        """The duplicate per-device loop module is gone (converged into agentic_loop)."""
        import importlib
        import pytest
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("unseen_university.devices.dicksimnel.toolloop")

    def test_ds_run_inference_delegates_to_domain(self):
        """DS._run_inference is a thin consumer: it delegates to CodingDomain.run() with
        domain='coding' and NO task_class/model/tier args (T-thin-ds-to-domain-consumer)."""
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice
        dev = DickSimnelDevice.__new__(DickSimnelDevice)
        ticket = {"id": "T-deleg", "description": "d", "tags": []}
        with patch("unseen_university.devices.dicksimnel.device.resolve_domain") as mock_resolve:
            domain = MagicMock()
            domain.run.return_value = "DONE: delegated"
            mock_resolve.return_value = domain
            out = dev._run_inference(ticket)
        mock_resolve.assert_called_once_with("coding")
        domain.run.assert_called_once_with(ticket, agent_id="dicksimnel")
        assert out == "DONE: delegated"

    def test_ds_run_inference_halt_returns_none(self):
        """A domain HALT (None) is relayed unchanged — worker_listener declines on None."""
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice
        dev = DickSimnelDevice.__new__(DickSimnelDevice)
        with patch("unseen_university.devices.dicksimnel.device.resolve_domain") as mock_resolve:
            domain = MagicMock()
            domain.run.return_value = None
            mock_resolve.return_value = domain
            assert dev._run_inference({"id": "T-halt", "description": "d", "tags": []}) is None

    def test_ds_holds_no_prompt_or_selection_logic(self):
        """DS is thin: no prompt-building / skill-loading / selection logic remains on it
        (T-thin-ds-to-domain-consumer completion criterion — the coding path is the domain's)."""
        import unseen_university.devices.dicksimnel.device as ds
        from unseen_university.devices.dicksimnel.device import DickSimnelDevice
        assert not hasattr(DickSimnelDevice, "_build_system_prompt")
        assert not hasattr(DickSimnelDevice, "skill_load")
        assert not hasattr(ds, "SYSTEM_PROMPT")
        assert not hasattr(DickSimnelDevice, "_IBD_PREAMBLE")

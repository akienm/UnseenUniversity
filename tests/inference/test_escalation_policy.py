"""EscalationPolicy proof (T-inference-escalation-policy-object).

Escalation becomes DATA carried by the domain, not control flow scattered across the walk as
ad-hoc bools. One object with two axes — ``escalates`` (does a capability wall advance a rung?)
and ``on_wall`` (the terminal when the walk can go no further) — collapses two retired flags:
harvest_mode → HARVEST_POLICY, and the escalation_allowed pin → NO_ESCALATION_POLICY. Only the
CAPABILITY-wall disposition is policy-driven; availability/cost handling stays universal.

These tests drive the real BaseDomain escalation walk through a recording subclass whose every
attempt is a capability wall (no real inference — fully hermetic). ``system_alarms.raise_alarm``
is patched so the DEFAULT ceiling walk stays offline and its alarm can be COUNTED.

PROOF NODE: ``test_three_policies_yield_distinct_walk_behavior`` — the SAME capability-wall
request under DEFAULT vs NO_ESCALATION vs HARVEST yields three observably different outcomes,
one rung advanced per wall (no double-spend), and exactly ONE terminal alarm (DEFAULT only).
Red (pre-impl there is no policy object — the guarded import fails → AssertionError) → green.

The policy symbols are imported FUNCTION-LOCAL and guarded on purpose: proof_emitter reverts the
impl (deleting escalation_policy.py), and a module-scope import would turn the whole file's
collection into a CollectionError instead of a clean per-node AssertionError (the new-symbol
trap; T-inference-typed-no-path-result learned this).
"""
from __future__ import annotations

import logging
from unittest.mock import patch

from unseen_university.devices.inference.domains.agentic_loop import LOOP_ESCALATE, LoopResult
from unseen_university.devices.inference.domains.base import BaseDomain, DomainPrompts

_TICKET = {"id": "T-esc-policy-proof", "title": "always-fails", "tags": [], "description": "d"}


class _WallDomain(BaseDomain):
    """A generalist domain whose every attempt is a capability wall, recording each hop.

    Overrides only ``_run_attempt`` and ``prompts`` — the escalation walk in BaseDomain.run is
    exactly the code under proof. ``__init__`` runs only when instantiated (inside a guarded
    test body), so this class definition is safe to import even when the policy object is absent.
    """

    def __init__(self, *, policy) -> None:
        super().__init__(name="", escalation_policy=policy)
        self.hops_seen: list[int] = []

    @property
    def prompts(self) -> DomainPrompts:
        return DomainPrompts(system="test system")

    def _run_attempt(self, *, system_prompt, ticket, ticket_id, agent_id,
                     escalation_hop, prior_attempt, cwd=None) -> LoopResult:
        self.hops_seen.append(escalation_hop)
        return LoopResult(LOOP_ESCALATE, text="could not finish")  # → classifies as 'capability'


def _walk(policy, mem_root, monkeypatch):
    """Run the walk under `policy` in an isolated memory/corpus root. Returns (hops, alarms, rungs)."""
    from unseen_university.devices.inference.domains.stuck_ladder import read_rung_choices

    monkeypatch.setenv("UU_MEMORY_ROOT", str(mem_root))
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(mem_root / "corpus"))
    d = _WallDomain(policy=policy)
    with patch("unseen_university.system_alarms.raise_alarm") as alarm:
        result = d.run(_TICKET)
    return d.hops_seen, alarm.call_count, len(read_rung_choices(root=None)), result


def test_three_policies_yield_distinct_walk_behavior(tmp_path, monkeypatch):
    """PROOF: DEFAULT vs NO_ESCALATION vs HARVEST — same wall, three asserted-distinct outcomes.

    Pre-impl there is no EscalationPolicy object; the guarded import fails and this asserts a
    clean RED. Post-impl the three policies drive the one walk to three different terminals.
    """
    try:
        from unseen_university.devices.inference.domains.escalation_policy import (
            DEFAULT_POLICY,
            HARVEST_POLICY,
            NO_ESCALATION_POLICY,
        )
    except ImportError:
        assert False, "EscalationPolicy object not implemented — no policy to drive the walk"

    # DEFAULT — the worker ladder is code→design→frontier; a wall at each advances exactly one
    # rung, then past-the-top fires the single capability-ceiling alarm.
    d_hops, d_alarms, d_rungs, d_result = _walk(DEFAULT_POLICY, tmp_path / "default", monkeypatch)
    assert d_hops == [0, 1, 2], f"DEFAULT must walk one rung per wall, got {d_hops}"
    assert d_hops == list(range(len(d_hops))), f"one rung per wall (no double-spend): {d_hops}"
    assert d_alarms == 1, f"DEFAULT ceiling must fire exactly ONE terminal alarm, got {d_alarms}"
    assert d_rungs == 0, "DEFAULT does not route to the harvest stuck-ladder"
    assert d_result is None  # ceiling halt

    # NO_ESCALATION — one attempt at the seed rung, then a SILENT halt: no rung advance, no
    # alarm, no stuck-ladder record. This terminal did not exist before the policy object.
    n_hops, n_alarms, n_rungs, n_result = _walk(NO_ESCALATION_POLICY, tmp_path / "noesc", monkeypatch)
    assert n_hops == [0], f"NO_ESCALATION must stay at the seed rung, got {n_hops}"
    assert n_alarms == 0, f"NO_ESCALATION halts SILENTLY — zero alarms, got {n_alarms}"
    assert n_rungs == 0, "NO_ESCALATION does not route to the harvest stuck-ladder"
    assert n_result is None

    # HARVEST — one attempt, then the wall routes to the stuck-ladder (one record) with no
    # alarm. This is what distinguishes it from NO_ESCALATION: same hops, but a rung record.
    h_hops, h_alarms, h_rungs, h_result = _walk(HARVEST_POLICY, tmp_path / "harvest", monkeypatch)
    assert h_hops == [0], f"HARVEST stays at the fixed tier, got {h_hops}"
    assert h_alarms == 0, f"a harvested wall is not an incident — zero alarms, got {h_alarms}"
    assert h_rungs == 1, f"HARVEST routes the wall to the stuck-ladder — one record, got {h_rungs}"
    assert h_result is None

    # The three are genuinely distinct: DEFAULT escalates (len 3), the other two pin (len 1);
    # NO_ESCALATION and HARVEST share hops but differ on the stuck-ladder record.
    assert d_hops != n_hops and d_hops != h_hops
    assert (n_hops, n_rungs) != (h_hops, h_rungs)


def test_policy_objects_have_two_axes():
    """The policy is DATA: name + escalates (bump rule) + on_wall (terminal), nothing else."""
    from unseen_university.devices.inference.domains.escalation_policy import (
        DEFAULT_POLICY,
        HARVEST_POLICY,
        NO_ESCALATION_POLICY,
        ON_WALL_CEILING,
        ON_WALL_HARVEST,
        ON_WALL_SILENT,
    )
    assert (DEFAULT_POLICY.escalates, DEFAULT_POLICY.on_wall) == (True, ON_WALL_CEILING)
    assert (NO_ESCALATION_POLICY.escalates, NO_ESCALATION_POLICY.on_wall) == (False, ON_WALL_SILENT)
    assert (HARVEST_POLICY.escalates, HARVEST_POLICY.on_wall) == (False, ON_WALL_HARVEST)


def test_policy_is_frozen():
    """A policy is an immutable value object — a shared singleton must not be mutated in place."""
    import dataclasses

    from unseen_university.devices.inference.domains.escalation_policy import DEFAULT_POLICY
    try:
        DEFAULT_POLICY.escalates = False  # type: ignore[misc]
        assert False, "EscalationPolicy must be frozen"
    except dataclasses.FrozenInstanceError:
        pass


def test_default_domain_carries_default_policy():
    """The general domain owns the DEFAULT policy (Amendment 2: the default IS the general domain)."""
    from unseen_university.devices.inference.domains.escalation_policy import DEFAULT_POLICY

    assert BaseDomain().escalation_policy is DEFAULT_POLICY


def test_no_escalation_writes_no_escalation_wall_resolution(tmp_path, monkeypatch):
    """The NO_ESCALATION terminal is recorded honestly — a distinct resolution, not a ceiling."""
    from unseen_university.devices.inference.domains.escalation_policy import NO_ESCALATION_POLICY
    from unseen_university.devices.inference.run_record import RESOLUTION_NO_ESCALATION_WALL

    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(tmp_path / "corpus"))
    captured = {}
    d = _WallDomain(policy=NO_ESCALATION_POLICY)
    # Capture the resolution the walk stamps on its run record.
    from unseen_university.devices.inference import run_record as rr

    orig = rr.RunRecord.write

    def _spy(self):
        captured["resolution"] = self.resolution
        return None  # do not touch disk

    monkeypatch.setattr(rr.RunRecord, "write", _spy)
    with patch("unseen_university.system_alarms.raise_alarm") as alarm:
        d.run(_TICKET)
    assert captured["resolution"] == RESOLUTION_NO_ESCALATION_WALL
    alarm.assert_not_called()

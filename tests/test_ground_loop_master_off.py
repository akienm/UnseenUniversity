"""Proof test for T-ground-loop-master-off.

Behavioral claim: GroundLoop.run_once() consults the (flat-file) HaltRegistry
under agent_id "ground_loop" and, when halted, suppresses ALL restart behavior
— it does not scan/register plugins, tick daemons, or run the runme supervisor.
When not halted it runs the full cycle. This is the master-off Akien asked for:
flip the flag, the loop stops restarting peers so they can be updated.

A hollow implementation (ignores the halt flag) keeps restarting peers while
halted — the `calls == []` assertion catches it. Authentic red.
"""
from __future__ import annotations

from unseen_university.devices.ground_loop.daemon import GroundLoop


class _FakeHalt:
    def __init__(self, halted: bool, reason: str = "") -> None:
        self._state = (halted, reason)
        self.queried_for = None

    def is_halted(self, agent_id: str):
        self.queried_for = agent_id
        return self._state


def _instrument(gl, calls):
    gl._scan_plugins = lambda: calls.append("scan")
    gl._tick_daemons = lambda: calls.append("tick")
    gl._supervisor.scan = lambda: calls.append("supervise")


def test_master_off_suppresses_all_restart_work_when_halted():
    halt = _FakeHalt(True, "updating ground_loop")
    gl = GroundLoop(halt_registry=halt)
    calls: list[str] = []
    _instrument(gl, calls)

    gl.run_once()

    assert calls == [], (
        f"master-off must suppress all restart work when halted; ran: {calls}"
    )
    # It consulted the registry under the canonical "ground_loop" agent_id.
    assert halt.queried_for == "ground_loop"


def test_runs_full_cycle_when_not_halted():
    gl = GroundLoop(halt_registry=_FakeHalt(False))
    calls: list[str] = []
    _instrument(gl, calls)

    gl.run_once()

    assert calls == ["scan", "tick", "supervise"], (
        f"must run the full restart cycle when not halted; ran: {calls}"
    )

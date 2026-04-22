"""Tests for wild_igor.igor.cognition.pursuits — MVP behavior.

Covers spawn→commitment dopamine, completion path, abandonment path,
suspend/resume, parent/child nesting, and the disabled-gate fallback.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition import pursuits as mod  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_registry_and_flag(monkeypatch):
    """Each test gets a clean registry; default to enabled unless a test
    overrides via monkeypatch."""
    mod._registry.clear()
    monkeypatch.setenv("IGOR_PURSUITS_ENABLED", "true")
    yield
    mod._registry.clear()


def test_spawn_fires_commitment_dopamine():
    p = mod.spawn(
        name="test_action",
        entry_stimulus={"kind": "test"},
        goal_facia=lambda s: s.get("done") is True,
    )
    assert p.status == "active"
    assert len(p.dopamine_trace) == 1
    assert p.dopamine_trace[0].kind == "commitment"
    assert p.dopamine_trace[0].magnitude > 0


def test_spawn_returns_disabled_when_gate_off(monkeypatch):
    monkeypatch.setenv("IGOR_PURSUITS_ENABLED", "false")
    p = mod.spawn(
        name="gated_off",
        entry_stimulus={},
        goal_facia=lambda s: True,
    )
    assert p.status == "disabled"
    # Disabled pursuit is NOT registered — registry stays empty
    assert mod._registry.get(p.id) is None


def test_evaluate_completion_fires_completion_when_test_passes():
    p = mod.spawn(
        name="will_complete",
        entry_stimulus={},
        goal_facia=lambda s: s.get("done") is True,
    )
    status = p.evaluate_completion({"done": True})
    assert status == "completed"
    kinds = [e.kind for e in p.dopamine_trace]
    assert "commitment" in kinds
    assert "completion" in kinds


def test_evaluate_completion_fires_abandonment_when_test_fails():
    p = mod.spawn(
        name="will_abandon",
        entry_stimulus={},
        goal_facia=lambda s: s.get("done") is True,
    )
    status = p.evaluate_completion({"done": False})
    assert status == "abandoned"
    kinds = [e.kind for e in p.dopamine_trace]
    assert "abandonment" in kinds


def test_evaluate_completion_idempotent_after_terminal_state():
    p = mod.spawn(
        name="once_only",
        entry_stimulus={},
        goal_facia=lambda s: True,
    )
    p.evaluate_completion({})
    # Second call should return the existing status without firing again
    completions_before = sum(1 for e in p.dopamine_trace if e.kind == "completion")
    p.evaluate_completion({})
    completions_after = sum(1 for e in p.dopamine_trace if e.kind == "completion")
    assert completions_before == completions_after == 1


def test_suspend_and_resume():
    p = mod.spawn(
        name="suspendable",
        entry_stimulus={},
        goal_facia=lambda s: True,
    )
    p.suspend()
    assert p.status == "suspended"
    p.resume()
    assert p.status == "active"


def test_parent_suspended_when_child_spawns():
    parent = mod.spawn(
        name="parent",
        entry_stimulus={},
        goal_facia=lambda s: True,
    )
    child = mod.spawn(
        name="child",
        entry_stimulus={},
        goal_facia=lambda s: True,
        parent_pursuit=parent.id,
    )
    assert parent.status == "suspended"
    assert child.id in parent.sub_pursuits


def test_child_completion_fires_subgoal_on_parent():
    parent = mod.spawn(
        name="parent",
        entry_stimulus={},
        goal_facia=lambda s: True,
    )
    child = mod.spawn(
        name="child",
        entry_stimulus={},
        goal_facia=lambda s: True,
        parent_pursuit=parent.id,
    )
    child.evaluate_completion({})
    # Parent should have received a subgoal event
    parent_kinds = [e.kind for e in parent.dopamine_trace]
    assert "subgoal" in parent_kinds


def test_resume_parent_helper():
    parent = mod.spawn(
        name="parent",
        entry_stimulus={},
        goal_facia=lambda s: True,
    )
    child = mod.spawn(
        name="child",
        entry_stimulus={},
        goal_facia=lambda s: True,
        parent_pursuit=parent.id,
    )
    assert parent.status == "suspended"
    mod.resume_parent(child)
    assert parent.status == "active"


def test_dopamine_subscribers_receive_events():
    received: list[mod.DopamineEvent] = []
    mod._registry.subscribe(received.append)
    p = mod.spawn(
        name="subscribed",
        entry_stimulus={},
        goal_facia=lambda s: True,
    )
    p.evaluate_completion({})
    kinds = [e.kind for e in received]
    assert kinds == ["commitment", "completion"]


def test_subscriber_exception_does_not_break_emit():
    def broken(_):
        raise RuntimeError("subscriber blew up")

    mod._registry.subscribe(broken)
    p = mod.spawn(
        name="resilient",
        entry_stimulus={},
        goal_facia=lambda s: True,
    )
    # Should not raise; should still complete normally
    status = p.evaluate_completion({})
    assert status == "completed"


def test_goal_facia_exception_counts_as_not_done():
    def raising(_):
        raise ValueError("bad predicate")

    p = mod.spawn(
        name="broken_goal",
        entry_stimulus={},
        goal_facia=raising,
    )
    status = p.evaluate_completion({})
    assert status == "abandoned"


def test_active_pursuits_list():
    a = mod.spawn(name="a", entry_stimulus={}, goal_facia=lambda s: True)
    b = mod.spawn(name="b", entry_stimulus={}, goal_facia=lambda s: True)
    b.suspend()
    active = mod._registry.active()
    assert a in active
    assert b not in active


def test_record_action_appends():
    p = mod.spawn(name="a", entry_stimulus={}, goal_facia=lambda s: True)
    p.record_action({"engram": "PROC_TEST", "score": 0.7})
    assert len(p.actions_taken) == 1
    assert p.actions_taken[0]["engram"] == "PROC_TEST"

"""Tests for the reply-forms-Pursuit wrap (T-reply-forms-pursuit).

main._process_network_msg is too heavy to instantiate here (full Igor
stack — cortex, user_ctx_mgr, thread buffers, LLM), so these tests
mirror the EXACT wrap pattern the method uses: look up active Pursuits,
spawn reply Pursuit as child of most-recent active, evaluate_completion +
resume_parent in finally. That pattern is what fixes the reply-eats-
progress bug — tests lock it down.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition import pursuits as pmod  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    pmod._registry.clear()
    monkeypatch.setenv("IGOR_PURSUITS_ENABLED", "true")
    yield
    pmod._registry.clear()


def _run_reply_wrap(
    response: str,
    author: str = "akien",
    source: str = "web",
    thread_id: str = "t1",
):
    """Mirror the wrap pattern in main._process_network_msg verbatim."""
    _reply_state: dict = {"delivered": False}
    _active_pursuits = pmod.registry().active()
    _reply_parent = (
        max(_active_pursuits, key=lambda p: p.commitment_ts)
        if _active_pursuits
        else None
    )
    _reply_pursuit = pmod.spawn(
        name=f"reply_to_{author}",
        entry_stimulus={"source": source, "author": author, "thread_id": thread_id},
        goal_facia=lambda s: s.get("delivered") is True,
        parent_pursuit=(_reply_parent.id if _reply_parent else None),
    )
    try:
        # Simulate _process(...) returning response
        _reply_state["delivered"] = bool(response)
    finally:
        _reply_pursuit.evaluate_completion(_reply_state)
        pmod.resume_parent(_reply_pursuit)
    return _reply_pursuit


def test_reply_completes_when_response_nonempty():
    p = _run_reply_wrap(response="hello world")
    assert p.status == "completed"


def test_reply_abandons_when_response_empty():
    p = _run_reply_wrap(response="")
    assert p.status == "abandoned"


def test_reply_without_active_parent_is_standalone():
    reply = _run_reply_wrap(response="hi")
    assert reply.parent_pursuit is None
    assert reply.status == "completed"


def test_reply_attaches_to_active_parent_and_resumes_it():
    """The core bug fix: in-flight Pursuit survives across reply."""
    parent = pmod.spawn(
        name="address_boredom",
        entry_stimulus={"trigger": "low_arousal"},
        goal_facia=lambda s: s.get("posted") is True,
    )
    assert parent.status == "active"

    reply = _run_reply_wrap(response="answering mid-boredom")

    assert reply.parent_pursuit == parent.id
    assert parent.sub_pursuits == [reply.id]
    assert reply.status == "completed"
    # Parent was suspended on spawn, lifted back by resume_parent
    assert parent.status == "active"


def test_reply_selects_most_recent_active_pursuit_as_parent():
    older = pmod.spawn(name="older", entry_stimulus={}, goal_facia=lambda s: False)
    time.sleep(0.01)
    newer = pmod.spawn(name="newer", entry_stimulus={}, goal_facia=lambda s: False)

    reply = _run_reply_wrap(response="x")

    assert reply.parent_pursuit == newer.id
    # Older was never touched
    assert older.status == "active"


def test_reply_wrap_is_noop_when_gate_disabled(monkeypatch):
    monkeypatch.setenv("IGOR_PURSUITS_ENABLED", "false")
    reply = _run_reply_wrap(response="hi")
    assert reply.status == "disabled"
    assert pmod.registry().active() == []


def test_exception_during_process_fires_abandonment():
    """If _process raises, the finally clause still closes out the Pursuit."""
    _reply_state: dict = {"delivered": False}
    reply = pmod.spawn(
        name="reply_to_akien",
        entry_stimulus={},
        goal_facia=lambda s: s.get("delivered") is True,
    )
    with pytest.raises(RuntimeError):
        try:
            raise RuntimeError("simulated _process failure")
        finally:
            reply.evaluate_completion(_reply_state)
            pmod.resume_parent(reply)

    assert reply.status == "abandoned"


def test_completion_dopamine_fires_subgoal_on_parent():
    """Parent should receive a subgoal dopamine event when child completes."""
    parent = pmod.spawn(
        name="address_boredom",
        entry_stimulus={},
        goal_facia=lambda s: s.get("posted") is True,
    )
    parent_trace_len_before = len(parent.dopamine_trace)

    _run_reply_wrap(response="ok")

    # Parent should have received a subgoal event (note="child:reply_to_akien")
    subgoal_events = [ev for ev in parent.dopamine_trace if ev.kind == "subgoal"]
    assert len(subgoal_events) == 1
    assert "reply_to_akien" in subgoal_events[0].note
    assert len(parent.dopamine_trace) > parent_trace_len_before

"""T-pe-chain-single-step-debugger — tests.

Inject a stimulus, run pe_chain to a breakpoint, advance one step at a time,
inspect the basket and 7 aspects at each pause.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wild_igor.igor.tools import pe_chain_debugger as dbg

# ── Helpers ──────────────────────────────────────────────────────────────────


def _noop(basket):
    """Generic passthrough used to stub pe_* functions in step tests."""
    return basket


@pytest.fixture
def stub_steps(monkeypatch):
    """Replace each pe_* step with a stub that writes a marker into basket.

    Lets us assert the debugger drives the engram sequence without touching
    the real pe_chain side-effects (cc_queue, cortex, etc.).
    """
    stubs = {}

    def make_stub(name):
        def _stub(basket):
            basket.setdefault("steps_run", []).append(name)
            basket[f"{name.lower()}_output"] = f"synthetic-{name}"
            return basket

        return _stub

    new_steps = []
    for name, _ in dbg.STEPS:
        stub = make_stub(name)
        stubs[name] = stub
        new_steps.append((name, stub))
    monkeypatch.setattr(dbg, "STEPS", new_steps)
    return stubs


# ── start() + breakpoint semantics ───────────────────────────────────────────


class TestStart:
    def test_unknown_breakpoint_returns_error(self):
        r = dbg.start("T-foo", breakpoint="NOT_A_REAL_STEP")
        assert r["ok"] is False
        assert "unknown breakpoint" in r["error"]

    def test_start_runs_until_breakpoint(self, stub_steps):
        r = dbg.start("T-foo", breakpoint="HYPOTHESIZE")
        assert r["ok"] is True
        snap = r["snapshot"]
        steps_run = snap["basket"]["steps_run"]
        # Runs ENTRY through HYPOTHESIZE inclusive (OBSERVE precedes HYPOTHESIZE)
        assert steps_run == [
            "ENTRY",
            "CLAIM",
            "READ_TICKET",
            "PLAN",
            "SITUATE",
            "OBSERVE",
            "HYPOTHESIZE",
        ]
        assert snap["last_step"] == "HYPOTHESIZE"
        assert snap["next_step"] == "IMPLEMENT"
        assert snap["finished"] is False

    def test_start_with_end_runs_whole_chain(self, stub_steps):
        r = dbg.start("T-foo", breakpoint="END")
        snap = r["snapshot"]
        assert snap["finished"] is True
        assert snap["next_step"] is None
        # All steps run
        assert snap["basket"]["steps_run"] == [name for name, _ in dbg.STEPS]

    def test_start_stores_session_id(self, stub_steps):
        r = dbg.start("T-foo", breakpoint="SITUATE")
        assert r["session_id"].startswith("dbg-")
        # Session is retrievable
        snap = dbg.snapshot(r["session_id"])
        assert snap["ok"] is True


# ── step_next() ──────────────────────────────────────────────────────────────


class TestStepNext:
    def test_step_next_advances_one_engram(self, stub_steps):
        r = dbg.start("T-foo", breakpoint="PLAN")
        sid = r["session_id"]
        before = r["snapshot"]["step_index"]

        r2 = dbg.step_next(sid)
        assert r2["ok"] is True
        after = r2["snapshot"]["step_index"]
        assert after == before + 1
        assert r2["snapshot"]["last_step"] == "SITUATE"

    def test_step_next_increments_history(self, stub_steps):
        r = dbg.start("T-foo", breakpoint="ENTRY")
        sid = r["session_id"]
        h1 = dbg.history(sid)["steps"]
        dbg.step_next(sid)
        h2 = dbg.history(sid)["steps"]
        assert h2 == h1 + 1

    def test_step_next_on_unknown_session_returns_error(self):
        r = dbg.step_next("dbg-does-not-exist")
        assert r["ok"] is False
        assert "unknown session" in r["error"]

    def test_step_next_after_finish_reports_no_more_steps(self, stub_steps):
        r = dbg.start("T-foo", breakpoint="END")
        sid = r["session_id"]
        r2 = dbg.step_next(sid)
        # Already finished; still returns ok with a note
        assert r2["ok"] is True
        assert "note" in r2


# ── snapshot structure ────────────────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_contains_expected_keys(self, stub_steps):
        r = dbg.start("T-foo", breakpoint="SITUATE")
        snap = r["snapshot"]
        for key in (
            "session_id",
            "ticket_id",
            "step_index",
            "last_step",
            "next_step",
            "breakpoint",
            "finished",
            "basket",
            "aspects",
            "ts",
        ):
            assert key in snap, f"missing key: {key}"

    def test_snapshot_is_deep_copy_of_basket(self, stub_steps):
        r = dbg.start("T-foo", breakpoint="CLAIM")
        sid = r["session_id"]
        snap_basket = r["snapshot"]["basket"]
        # Mutate the snapshot; the session's live basket should be unaffected
        snap_basket["injected"] = "bad"
        live = dbg.snapshot(sid)["snapshot"]["basket"]
        assert "injected" not in live

    def test_aspects_dict_structure(self, stub_steps):
        r = dbg.start("T-foo", breakpoint="ENTRY")
        aspects = r["snapshot"]["aspects"]
        # Each aspect call returns a dict with an ok field
        for name, result in aspects.items():
            assert isinstance(result, dict)
            assert "ok" in result, f"aspect {name} missing 'ok'"


# ── session lifecycle ────────────────────────────────────────────────────────


class TestLifecycle:
    def test_abandon_removes_session(self, stub_steps):
        r = dbg.start("T-foo", breakpoint="ENTRY")
        sid = r["session_id"]
        dbg.abandon(sid)
        r2 = dbg.snapshot(sid)
        assert r2["ok"] is False

    def test_list_sessions_returns_all_active(self, stub_steps):
        # Abandon any existing
        for s in dbg.list_sessions()["sessions"]:
            dbg.abandon(s["session_id"])
        s1 = dbg.start("T-foo", breakpoint="CLAIM")["session_id"]
        s2 = dbg.start("T-bar", breakpoint="PLAN")["session_id"]
        ids = {s["session_id"] for s in dbg.list_sessions()["sessions"]}
        assert s1 in ids and s2 in ids
        dbg.abandon(s1)
        dbg.abandon(s2)


# ── step_names enumerates canonical sequence ─────────────────────────────────


def test_step_names_covers_canonical_sequence():
    names = dbg.step_names()
    for expected in (
        "ENTRY",
        "CLAIM",
        "READ_TICKET",
        "PLAN",
        "SITUATE",
        "HYPOTHESIZE",
        "IMPLEMENT",
        "OBSERVE",
    ):
        assert expected in names


# ── Exception in a step doesn't kill the debugger ────────────────────────────


class TestRobustness:
    def test_step_exception_is_caught_and_recorded(self, monkeypatch):
        """If a pe_* function raises, debugger records it and continues."""

        def bad(basket):
            raise ValueError("boom")

        def ok(basket):
            basket.setdefault("steps_run", []).append("ok")
            return basket

        new_steps = [
            ("ENTRY", ok),
            ("CLAIM", bad),
            ("READ_TICKET", ok),
            ("PLAN", ok),
            ("SITUATE", ok),
            ("HYPOTHESIZE", ok),
            ("IMPLEMENT", ok),
            ("OBSERVE", ok),
        ]
        monkeypatch.setattr(dbg, "STEPS", new_steps)
        r = dbg.start("T-foo", breakpoint="READ_TICKET")
        assert r["ok"] is True
        assert "debugger_error" in r["snapshot"]["basket"]
        assert "boom" in r["snapshot"]["basket"]["debugger_error"]


# ── Tool registration ────────────────────────────────────────────────────────


def test_tools_registered():
    from wild_igor.igor.tools.registry import registry

    assert registry.get("pe_chain_debug_start") is not None
    assert registry.get("pe_chain_debug_step") is not None

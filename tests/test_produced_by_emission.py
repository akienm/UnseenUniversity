"""Proof for T-produced-by-emission-sweep (feedback-edges contract).

Every NEW store artifact carries its backward edge `produced_by` at emission
time, stamped by the three chokepoints: memory_emit.emit (generic + fallback),
ticket_store._envelope (ticket -> its decision), and the proof emitter (proof ->
its ticket). Additive: readers tolerate legacy artifacts that lack the field.

RED before the sweep: emit()/envelope produce records with no produced_by, so the
presence assertions fail with AssertionError (the symbols exist — behavior is
wrong). GREEN once the chokepoints stamp it.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devlab", "claudecode"))

import memory_emit  # noqa: E402


def test_emit_stamps_explicit_produced_by(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    p = memory_emit.emit("decisions", "cc.0", {"id": "D-x"}, kind="decision",
                         produced_by="intent:I-foo")
    assert json.load(open(p))["produced_by"] == "intent:I-foo"


def test_emit_defaults_to_session_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    p = memory_emit.emit("notes", "cc.0", {"text": "n"}, kind="note")
    # .get so a reverted (unstamped) chokepoint fails with AssertionError, not
    # KeyError — the authentic-red form the proof gate requires.
    assert json.load(open(p)).get("produced_by") == "session:cc.0"


def test_ticket_envelope_produced_by_is_its_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    from unseen_university import ticket_store
    ticket_store.write({"id": "T-x", "status": "sprint",
                        "decision_id": "D-x", "title": "t"})
    files = [os.path.join(r, f) for r, _, fs in os.walk(tmp_path)
             for f in fs if "T-x" in f]
    env = json.load(open(files[0]))
    assert env["produced_by"] == "D-x"
    assert env["body"]["id"] == "T-x"  # body still reads normally


def test_ticket_without_decision_falls_back_to_session(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    from unseen_university import ticket_store
    ticket_store.write({"id": "T-y", "status": "sprint", "title": "t",
                        "created_by": "cc.0"})
    files = [os.path.join(r, f) for r, _, fs in os.walk(tmp_path)
             for f in fs if "T-y" in f]
    assert json.load(open(files[0]))["produced_by"] == "session:cc.0"


def test_legacy_artifact_without_produced_by_still_reads(tmp_path, monkeypatch):
    """Additive contract: a reader must tolerate the field's absence."""
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    from unseen_university import ticket_store
    tdir = tmp_path / "tickets"
    tdir.mkdir()
    legacy = tdir / "cc.0.T-legacy.20260101.000000000000.json"
    legacy.write_text(json.dumps({
        "id": "cc.0.T-legacy.20260101.000000000000",
        "emitter": "cc.0", "namespace": ["T-legacy"], "category": "tickets",
        "kind": "ticket", "links": {"tickets": ["T-legacy"]},
        "body": {"id": "T-legacy", "status": "sprint", "title": "old"},
        # NOTE: no produced_by
    }), encoding="utf-8")
    got = ticket_store.read("T-legacy")
    assert got is not None and got["id"] == "T-legacy"

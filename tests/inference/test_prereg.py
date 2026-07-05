"""
prereg — prospective pre-registration: predict before the answer exists, grade by the UNCOUPLED
reality verdict, never by proof-on-close.

THE PROOF NODE is ``test_grade_ignores_bogus_proof_on_close_uses_reality_verdict``: proof-on-close
is mocked to return a bogus PASS; the grade must IGNORE it and report the reality verdict from
``corpus_verdict.verdict_strength``. GREEN: ``grounded`` tracks the uncoupled verdict (a green proof
that never bore consequence is TEST_GREEN, below the firewall floor, so grounded=False). RED (a
naive grader that trusts proof-on-close): the bogus PASS makes grounded=True and the assert fails —
the coupling the whole program removes, caught. The second load-bearing test pins the earliest-wins
firewall guard: a re-run's post-hoc prediction must not overwrite the fixed-before-the-answer one.
"""
from __future__ import annotations

import json

from unseen_university.devices.inference import prereg
from unseen_university.devices.inference.corpus_verdict import VerdictEvidence


def _read_prereg(root) -> list:
    recs = []
    for f in sorted(root.glob("*.prereg.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def test_grade_ignores_bogus_proof_on_close_uses_reality_verdict(tmp_path, monkeypatch):
    """The whole point: grade by the uncoupled reality verdict, not by proof-on-close."""
    import unseen_university.proof_store as proof_store_mod

    # Bogus proof-on-close: always PASSes (returns a valid proof). The grader must ignore it.
    monkeypatch.setattr(proof_store_mod, "best_valid_proof", lambda tid, repo: ({"body": {}}, []))

    root = tmp_path / "prereg"
    prereg.record_prediction("T-x", warm=None, files=["a.py"], plan="do a", root=root)

    class StubSource:
        # reality: a green proof exists but nothing bore consequence → TEST_GREEN (below firewall).
        def evidence_for(self, tid):
            return VerdictEvidence(has_green_proof=True)

    grade = prereg.PredictionGrader(evidence_source=StubSource(), root=root).grade("T-x")

    assert grade.verdict_strength == "TEST_GREEN"
    assert grade.grounded is False  # bogus PASS ignored; firewall floor (CONSEQUENCE_BEARING) not met


def test_grade_reads_earliest_prediction_not_latest(tmp_path, monkeypatch):
    """Firewall guard: a re-run appends, but the grade must read the FIXED-before-the-answer record."""
    root = tmp_path / "prereg"

    # First (fixed-before-the-answer) prediction, then a post-hoc-smarter re-run for the same ticket.
    prereg.record_prediction("T-iter", warm=None, files=["first.py"], plan="p1", root=root)
    # Force a strictly-later ts so ordering is unambiguous regardless of write speed.
    later = json.loads((sorted(root.glob("*.prereg.jsonl"))[0]).read_text().splitlines()[0])
    later.update({"ts": "2999-01-01T00:00:00+00:00", "files": ["second.py"], "id": "later"})
    with (sorted(root.glob("*.prereg.jsonl"))[0]).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(later) + "\n")

    class NullSource:
        def evidence_for(self, tid):
            return VerdictEvidence()

    grade = prereg.PredictionGrader(evidence_source=NullSource(), root=root).grade("T-iter")
    assert grade.prediction_found is True
    assert grade.predicted_files == ["first.py"]  # earliest wins, not ["second.py"]


def test_record_prediction_appends_and_roundtrips(tmp_path):
    root = tmp_path / "prereg"
    path = prereg.record_prediction(
        "T-a", warm=None, files=["x.py", "y.py"], plan="the plan", fingerprint="abc123", root=root
    )
    assert path is not None
    recs = _read_prereg(root)
    assert len(recs) == 1
    r = recs[0]
    assert r["schema"] == prereg.SCHEMA == "inference.prereg.v1"
    assert r["ticket_id"] == "T-a" and r["files"] == ["x.py", "y.py"] and r["warm"] is None
    assert r["domain"] == "coding"  # generalization hook — coding is the first domain, not the only one
    assert r["id"] and r["ts"] and r["fingerprint"] == "abc123"


def test_record_prediction_from_packet_extracts_fields(tmp_path):
    root = tmp_path / "prereg"
    packet = {
        "ticket_id": "T-pkt",
        "context_shortlist": [{"path": "mod/a.py"}, {"path": "mod/b.py"}],
        "proof_plan": {"test_plan": "assert the thing"},
        "determinism": {"fingerprint_sha256": "deadbeef"},
    }
    prereg.record_prediction_from_packet(packet, root=root)
    r = _read_prereg(root)[0]
    assert r["ticket_id"] == "T-pkt"
    assert r["files"] == ["mod/a.py", "mod/b.py"]
    assert r["plan"] == "assert the thing"
    assert r["fingerprint"] == "deadbeef"
    assert r["warm"] is None  # unknown at build-packet time — honest, not a proxy


def test_record_prediction_is_fail_soft(tmp_path):
    """A write error is swallowed and returns None — a lost prediction never breaks a sprint."""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir", encoding="utf-8")  # mkdir under it will fail
    assert prereg.record_prediction("T-z", warm=None, files=[], plan="", root=blocker) is None

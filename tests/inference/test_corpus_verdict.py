"""
corpus_verdict — the ground-truth join: reality-graded verdict_strength on corpus entries.

io_corpus records carry ``ticket_id`` but only a transport outcome; this module joins each
entry to the proof/ticket stores and stamps a ``verdict_strength`` on the coupling-to-reality
gradient llm_said_it < test_green < executed_live < consequence_bearing < akien_noticed.

THE PROOF NODE is ``test_wrong_but_noncrashing_scores_executed_live_not_consequence``: a
wrong-but-non-crashing entry (it ran — liveness — but nothing on its lines was ever revisited)
must score EXECUTED_LIVE, never CONSEQUENCE_BEARING. GREEN: the classifier requires a distinct
``consequence_signal`` (a future fact) for the top code rung. RED (a naive classifier that
treats mere execution/presence as consequence): it returns CONSEQUENCE_BEARING and the assert
fails — the self-consistency trap, caught. The other load-bearing test drives the real
GitEvidenceSource against a temp repo so the reality-join ships proven, not just the classifier.
"""
from __future__ import annotations

import json
import subprocess as sp

from unseen_university.devices.inference import corpus_verdict
from unseen_university.devices.inference.corpus_verdict import (
    CorpusVerdictReader,
    GitEvidenceSource,
    VerdictEvidence,
    VerdictStrength,
    classify,
)


def test_wrong_but_noncrashing_scores_executed_live_not_consequence():
    """The KEY discrimination: liveness is NOT consequence.

    A wrong-but-non-crashing entry ran (executed_live) but nothing on its lines was ever
    revisited (no consequence_signal). It must score EXECUTED_LIVE. A naive classifier that
    conflates execution/presence with consequence returns CONSEQUENCE_BEARING and fails here.
    """
    ev = VerdictEvidence(has_green_proof=True, executed_live=True, consequence_signal=False)
    assert classify(ev) == VerdictStrength.EXECUTED_LIVE
    assert classify(ev) is not VerdictStrength.CONSEQUENCE_BEARING


def test_classify_full_ladder():
    assert classify(VerdictEvidence()) == VerdictStrength.LLM_SAID_IT
    assert classify(VerdictEvidence(has_green_proof=True)) == VerdictStrength.TEST_GREEN
    assert classify(VerdictEvidence(has_green_proof=True, executed_live=True)) == VerdictStrength.EXECUTED_LIVE
    assert classify(VerdictEvidence(consequence_signal=True)) == VerdictStrength.CONSEQUENCE_BEARING
    assert classify(VerdictEvidence(akien_signal=True)) == VerdictStrength.AKIEN_NOTICED
    # strongest present signal wins even when weaker ones co-occur.
    strongest = VerdictEvidence(
        has_green_proof=True, executed_live=True, consequence_signal=True, akien_signal=True
    )
    assert classify(strongest) == VerdictStrength.AKIEN_NOTICED


def test_reality_tested_view_excludes_llm_said_it(tmp_path, monkeypatch):
    """The reality-tested view drops LLM_SAID_IT; full enrich keeps every entry stamped."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "20260705.io.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"ticket_id": "T-consequence", "id": "a"},
                {"ticket_id": "T-bare", "id": "b"},
                {"ticket_id": "", "id": "c"},  # unlinkable — no ticket join
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class StubSource:
        def evidence_for(self, tid):
            if tid == "T-consequence":
                return VerdictEvidence(has_green_proof=True, consequence_signal=True)
            return VerdictEvidence()  # T-bare: llm_said_it only

    # Treat every non-empty ticket_id as linked without needing a real ticket store.
    monkeypatch.setattr(corpus_verdict.ticket_store, "read", lambda tid: {"id": tid} if tid else None)

    reader = CorpusVerdictReader(corpus_root=corpus, evidence_source=StubSource())

    view_ids = {r["id"] for r in reader.reality_tested_view()}
    assert view_ids == {"a"}  # only the consequence_bearing entry crosses the floor

    stamped = {r["id"]: r["verdict_strength"] for r in reader.enrich()}
    assert stamped == {"a": "CONSEQUENCE_BEARING", "b": "LLM_SAID_IT", "c": "LLM_SAID_IT"}


def test_git_evidence_source_reality_join(tmp_path, monkeypatch):
    """Drive the REAL GitEvidenceSource against a temp repo + fixture proof store.

    Proves the reality-join, not just the pure classifier — and pins the honesty guard:
    executed_live stays False even though the code is committed and live in the tree
    (it is never fabricated from git topology).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    memroot = tmp_path / "memory"
    (memroot / "proofs").mkdir(parents=True)
    monkeypatch.setenv("UU_MEMORY_ROOT", str(memroot))

    def git(*args):
        return sp.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "impl.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "impl.py")
    git("commit", "-q", "-m", "impl")
    commit = git("rev-parse", "HEAD").stdout.strip()

    proof = {
        "id": "cc.0.thing.T-real.stamp",
        "links": {"tickets": ["T-real"]},
        "body": {"commit": commit, "impl_paths": ["impl.py"], "gates": [{"result": "green"}]},
    }
    (memroot / "proofs" / "cc.0.thing.T-real.stamp.json").write_text(json.dumps(proof), encoding="utf-8")

    src = GitEvidenceSource(repo_root=str(repo))

    # Case A: shipped, never revisited → green proof, NO consequence.
    ev = src.evidence_for("T-real")
    assert ev.has_green_proof is True
    assert ev.consequence_signal is False
    assert ev.executed_live is False  # honesty guard: not fabricated from ancestry
    assert classify(ev) == VerdictStrength.TEST_GREEN

    # Case B: a later commit touches the same lines → wrongness surfaced.
    (repo / "impl.py").write_text("x = 2  # corrected\n", encoding="utf-8")
    git("add", "impl.py")
    git("commit", "-q", "-m", "fix")
    ev2 = src.evidence_for("T-real")
    assert ev2.consequence_signal is True
    assert classify(ev2) == VerdictStrength.CONSEQUENCE_BEARING

    # An unknown ticket has no witness at all.
    assert classify(src.evidence_for("T-missing")) == VerdictStrength.LLM_SAID_IT

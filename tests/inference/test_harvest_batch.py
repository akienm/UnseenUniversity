"""Plumbing proof for the batch-harvest corpus generator (T-ds-harvest-corpus-batch).

The batch's contract: each seed's transcript lands in io_corpus correlated by ticket_id, and
ONLY the EVAL-split seeds' transcripts are sealed into the held-out eval slice (the reality-
uncoupled surface the classifier grades against). This drives run_batch() with a STUBBED
domain.run — no real inference — so the plumbing (per-seed capture + correct-SUBSET seal) is
proven hermetically. The distinction-bearing QUALITY of a real corpus is a separate measurement
confirmed by Akien's human-label pass; that is not what this proves.

PROOF NODE: with 1 eval seed + 1 dev seed, the sealed manifest covers EXACTLY the eval seed's
record (n==1), never the dev seed's. Red (a hollow runner that seals all corpus records → n==2,
or one that seals none → n==0) → green (n==1, and it is the eval ticket_id). The load-bearing
line is run_batch's `if r.get("ticket_id") in eval_ids` split.
"""
from __future__ import annotations

import sys
from pathlib import Path

# harvest_batch lives in devlab/claudecode and imports its sibling harvest_run by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "devlab" / "claudecode"))

import harvest_batch  # noqa: E402

from unseen_university.devices.inference.domains.escalation_policy import HARVEST_POLICY  # noqa: E402


class _StubDomain:
    """A domain whose .run captures one canned io_corpus transcript for the ticket, then declines."""

    escalation_policy = HARVEST_POLICY

    def run(self, ticket, *, cwd=None, agent_id="", urgency="normal"):
        from unseen_university.devices.inference import io_corpus

        io_corpus.capture({
            "ticket_id": ticket["id"], "role": "editor", "turn": 4, "outcome": "max_turns",
            "provider": "ollama", "model": "devstral-small-2:24b", "dollars": 0.0,
            "request": "...", "response": "...",
        })
        return None  # harvest wall → graceful terminal


def test_batch_seals_only_eval_split_transcripts(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_INFERENCE_CORPUS", str(tmp_path / "corpus"))
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path / "mem"))
    monkeypatch.setattr(
        "unseen_university.devices.inference.domains.resolve_domain",
        lambda name: _StubDomain(),
    )

    seeds = [
        {"id": "T-seed-eval", "class_intent": "design_stuck", "split": "eval",
         "title": "e", "description": "d", "scratch_files": {}},
        {"id": "T-seed-dev", "class_intent": "capability_stuck", "split": "dev",
         "title": "v", "description": "d", "scratch_files": {}},
    ]

    report = harvest_batch.run_batch(
        seeds, slice_name="test-slice", budget=8, seal_root=tmp_path / "slices",
    )

    # (a) every seed captured exactly one transcript, joined by ticket_id.
    assert all(s["n_records"] == 1 for s in report["summaries"]), report["summaries"]
    # (b) the sealed slice covers ONLY the eval-split seed — n==1, not 2 (seal-all) or 0 (seal-none).
    m = report["manifest"]
    assert m["n"] == 1, f"eval slice must seal only the eval-split transcript, got n={m['n']}"
    assert report["eval_seed_ids"] == ["T-seed-eval"]

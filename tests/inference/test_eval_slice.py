"""
eval_slice — seal a held-out slice so grounding data isn't silently spent by debugging.

THE PROOF NODE is ``test_read_past_budget_blocks_strict_slice``: a strict slice consulted beyond
its use-budget must BLOCK (raise BudgetExceeded) and record nothing. GREEN: the budget check fires
on the (budget+1)th read. RED (no budget check): the over-budget read succeeds silently — the
asymmetry spent invisibly, which is the exact failure this module exists to prevent. The other
tests pin the manifest's re-seal stability and the one-record-per-access read-log.
"""
from __future__ import annotations

import json

import pytest

from unseen_university.devices.inference.eval_slice import BudgetExceeded, EvalSlice


def _entries():
    return [
        {"id": "b2", "ticket_id": "T-x", "outcome": "ok"},
        {"id": "a1", "ticket_id": "T-y", "outcome": "ok"},
    ]


def test_manifest_hash_stable_across_reseal(tmp_path):
    """Sealing identical input twice yields the identical content_hash (order-independent)."""
    root = tmp_path / "eval"
    m1 = EvalSlice("s", budget=5, root=root).seal(_entries())
    # reseal with the SAME entries in a DIFFERENT order — hash must not move.
    m2 = EvalSlice("s", budget=5, root=root).seal(list(reversed(_entries())))
    assert m1["content_hash"] == m2["content_hash"]
    assert m1["n"] == 2 and m1["entry_ids"] == ["a1", "b2"]
    # a content change flips the hash (tamper-evident).
    changed = _entries()
    changed[0]["outcome"] = "error"
    m3 = EvalSlice("s2", budget=5, root=root).seal(changed)
    assert m3["content_hash"] != m1["content_hash"]


def test_each_read_appends_one_record(tmp_path):
    root = tmp_path / "eval"
    s = EvalSlice("s", budget=5, root=root)
    s.seal(_entries())
    s.read(by="cc.0", reason="debug replay miss")
    s.read(by="cc.0", reason="check verdict join")
    assert s.reads_used() == 2
    lines = (root / "s" / "reads.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["by"] == "cc.0" and rec["read_index"] == 0 and rec["over_budget"] is False


def test_read_past_budget_blocks_strict_slice(tmp_path):
    """THE proof node: a strict slice raises past budget and records nothing over the line."""
    root = tmp_path / "eval"
    s = EvalSlice("held-out", budget=2, strict=True, root=root)
    s.seal(_entries())
    s.read(by="cc.0", reason="first")
    s.read(by="cc.0", reason="second")
    assert s.reads_used() == 2
    with pytest.raises(BudgetExceeded):
        s.read(by="cc.0", reason="third — over budget")
    # blocked read is NOT recorded — it never counts as spent.
    assert s.reads_used() == 2


def test_lenient_slice_warns_and_records_overage(tmp_path):
    """A lenient slice does not block — it records the overage flagged, so the spend stays visible."""
    root = tmp_path / "eval"
    s = EvalSlice("soft", budget=1, strict=False, root=root)
    s.seal(_entries())
    s.read(by="cc.0", reason="within")
    over = s.read(by="cc.0", reason="over")  # no raise
    assert over["over_budget"] is True
    assert s.reads_used() == 2

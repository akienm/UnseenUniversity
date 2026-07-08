"""Proof for T-validity-sweep-day-close (validity-conditions contract, pull side).

The day-close sweep resolves each entry's validity_conditions against the current
world and FLAGS (annotates, never deletes) the broken ones. Hermetic: a tmp store
with entries covering all three condition types; a superseded artifact and a gone
path get flagged, a live path clears, a probeless fact is unresolvable; --apply
appends stale_flags in place without deleting; the summary line always prints.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devlab", "claudecode"))

import validity_sweep as vs  # noqa: E402


def _entry(store, cat, name, conditions, body=None):
    d = store / cat
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "id": f"cc.0.{name}.20260101.000000000000",
        "emitter": "cc.0", "category": cat, "kind": cat[:-1],
        "validity_conditions": conditions,
        "body": body or {"id": name},
    }
    (d / f"cc.0.{name}.20260101.000000000000.json").write_text(
        json.dumps(rec), encoding="utf-8")
    return rec


def _artifact(store, cat, aid, status):
    d = store / cat
    d.mkdir(parents=True, exist_ok=True)
    (d / f"cc.0.{aid}.20260101.000000000000.json").write_text(
        json.dumps({"id": f"cc.0.{aid}...", "category": cat,
                    "body": {"id": aid, "status": status}}), encoding="utf-8")


def test_broken_path_and_superseded_artifact_flag_but_live_path_clears(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    # A path that exists in the repo (this test file itself) -> clears.
    live = "tests/test_validity_sweep.py"
    _entry(tmp_path, "rules", "r-live", [{"type": "depends-on-path", "target": live}])
    _entry(tmp_path, "rules", "r-gone",
           [{"type": "depends-on-path", "target": "no/such/file_xyz.py"}])
    _entry(tmp_path, "decisions", "d-dep",
           [{"type": "depends-on-artifact", "target": "D-old"}])
    _artifact(tmp_path, "decisions", "D-old", "superseded-by-D-new")

    result = vs.sweep(str(tmp_path), apply=True, sweep_run="run1")
    assert result["checked"] == 3
    assert result["flagged"] == 2  # r-gone + d-dep; r-live clears
    ids = {e["id"] for e in result["flagged_entries"]}
    assert ids == {"r-gone", "d-dep"}
    assert vs.format_summary(result) == \
        "validity sweep: flagged=2 checked=3 unresolvable=0"


def test_missing_artifact_is_unresolvable_not_broken(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    _entry(tmp_path, "notes", "n-x",
           [{"type": "depends-on-artifact", "target": "D-vanished"}])
    result = vs.sweep(str(tmp_path), apply=False, sweep_run="run1")
    assert result["unresolvable"] == 1
    assert result["flagged"] == 1  # unresolvable still surfaces as a flag
    reason = result["flagged_entries"][0]["flags"][0]["reason"]
    assert "resolves to nothing" in reason


def test_probeless_fact_is_unresolvable(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    _entry(tmp_path, "rules", "r-fact",
           [{"type": "depends-on-fact", "target": "Igor is retired"}])
    result = vs.sweep(str(tmp_path), apply=False, sweep_run="run1")
    assert result["unresolvable"] == 1


def test_apply_annotates_in_place_and_never_deletes(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    _entry(tmp_path, "rules", "r-gone",
           [{"type": "depends-on-path", "target": "no/such/file_xyz.py"}])
    fp = tmp_path / "rules" / "cc.0.r-gone.20260101.000000000000.json"
    vs.sweep(str(tmp_path), apply=True, sweep_run="run1")
    assert fp.exists(), "annotate must never delete the entry"
    rec = json.loads(fp.read_text())
    assert len(rec["stale_flags"]) == 1
    assert rec["stale_flags"][0]["state"] == "broken"
    assert rec["validity_conditions"], "original conditions preserved"


def test_zero_flag_run_still_reports_count_line(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    _entry(tmp_path, "rules", "r-live",
           [{"type": "depends-on-path", "target": "tests/test_validity_sweep.py"}])
    result = vs.sweep(str(tmp_path), apply=False, sweep_run="run1")
    assert result["flagged"] == 0
    assert vs.format_summary(result) == \
        "validity sweep: flagged=0 checked=1 unresolvable=0"

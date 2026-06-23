"""Proof for T-path-moves-monitor (D-canonical-memory-consolidation).

The monitor flags a dev-process artifact at a retired path or outside the canonical
home, and is silent on a clean tree. RED on the stub scan() (returns []), GREEN on
the real detector.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "devlab" / "claudecode"))

import path_moves_monitor as pmm  # noqa: E402

_REGISTRY = {
    "canonical_home": "devlab/runtime/memory/",
    "retired_paths": ["lab/", "devlab/design_docs/", "devlab/design_docs_for_igor/"],
    "moves": [{"from": "lab/design_docs/decisions/", "to": "devlab/runtime/memory/decisions/"}],
    "artifact_suffixes": [".slate.txt", ".dsb"],
}


def test_scan_flags_misfiled_artifacts_and_is_silent_on_clean_tree():
    """Proof node (one intention): misfiled artifacts are flagged, a clean tree is silent."""
    misfiled = [
        # a decision emission JSON resurrected under the retired lab/ store:
        "lab/design_docs/decisions/cc.0.D-x.20260623.120000123456.json",
        # an emission JSON outside the canonical home entirely:
        "src/whatever/cc.0.D-y.20260101.000000000000.json",
        # the legacy .dsb log anywhere but the store:
        "devlab/design_docs_for_igor/decisions_log.dsb",
    ]
    found = pmm.scan(misfiled, _REGISTRY)
    flagged = {f["path"] for f in found}
    assert flagged == set(misfiled), f"monitor missed a misfiled artifact: {set(misfiled) - flagged}"
    assert any(f["reason"] == "under-retired-path" for f in found)
    assert any(f["reason"] == "artifact-outside-canonical-home" for f in found)
    # the retired-path finding suggests the canonical move target:
    lab_f = next(f for f in found if f["path"].startswith("lab/design_docs/decisions/"))
    assert lab_f["suggested"].startswith("devlab/runtime/memory/decisions/")

    # a clean tree (canonical artifacts + ordinary code) raises nothing:
    clean = [
        "devlab/runtime/memory/decisions/cc.0.D-z.20260623.120000000000.json",
        "devlab/runtime/memory/slates/20260623.slate.txt",
        "devlab/claudecode/path_moves_monitor.py",
        "unseen_university/rules/safeguards.py",  # 'rules' dir name must NOT false-positive
        "docs/notes/whatever.md",                 # 'notes' dir name must NOT false-positive
    ]
    assert pmm.scan(clean, _REGISTRY) == []


def test_run_is_fail_soft(monkeypatch):
    """A scan error returns [] — the monitor never raises into day-close."""
    monkeypatch.setattr(pmm, "scan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pmm.run(emit=False) == []


def test_real_repo_findings_have_zero_false_positives():
    """A noisy monitor is worse than none. Every finding on the live tree must be a
    GENUINE retired-path artifact (no legitimate code/store path leaks in). The
    retired-path files themselves are removed by T-retire-decision-folders /
    T-retire-designdocs-architecture — until then the monitor correctly flags them."""
    reg = pmm.load_registry()
    retired = tuple(reg["retired_paths"])
    findings = pmm.scan(pmm.git_tracked_files(), reg)
    stray = [f["path"] for f in findings if not f["path"].startswith(retired)]
    assert stray == [], f"monitor false-positived on non-retired paths: {stray[:10]}"

"""Proof for T-rules-store-materialize (rebuildability-diff F1, CRITICAL).

Every rule the audit skills cite as ``unseenuniversity/rules/<name>`` resolves to
a readable JSON artifact in the canonical store, and each artifact carries its
why (CP3). This test goes RED on the pre-materialization store (14 cited rule
names, only path_moves.json on disk — the gates enforced law a fresh builder
could not read) and GREEN once the 14 artifacts exist. The failing-checker test
pins that the checker itself can go red — a checker that passes on an empty
store would be the hollow-build signature.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "devlab" / "claudecode"))

import rules_citation_check as rcc  # noqa: E402


def test_every_skill_citation_resolves_to_a_rule_artifact():
    """The invariant: unresolved-citation count is 0 against the real store."""
    cites = rcc.collect_citations(str(_REPO / "skills"))
    assert cites, "citation scan found nothing — scanner regression, not a clean store"
    rules_root = str(_REPO / "devlab" / "runtime" / "memory" / "rules")
    unresolved = {c for c in cites if not rcc.resolve(c, rules_root)[0]}
    assert not unresolved, f"phantom rule citations: {sorted(unresolved)}"


def test_checker_goes_red_on_a_store_without_rule_artifacts(tmp_path):
    """The checker can fail: a store missing the artifacts reports them unresolved."""
    empty_rules = tmp_path / "rules"
    empty_rules.mkdir()
    ok, detail = rcc.resolve("safeguards", str(empty_rules))
    assert not ok and detail == "no artifact"


def test_every_rule_artifact_carries_its_why():
    """CP3 — every materialized rule states why it exists."""
    arts = glob.glob(str(_REPO / "devlab" / "runtime" / "memory" / "rules" / "cc.0.*.json"))
    assert len(arts) >= 14
    missing = [p for p in arts if not json.load(open(p)).get("body", {}).get("why")]
    assert not missing, f"rule artifacts without a why: {missing}"

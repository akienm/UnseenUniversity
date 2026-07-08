"""Proof for T-builder-memory-repo-residency (rebuildability-diff F2, CRITICAL).

A fresh builder on any box must inherit the corrected working style from the REPO,
not from one instance's private ~/.claude memory dir. This test goes RED on the
pre-residency store (no triage table, no dispatch/autonomy/git-workflow/design-doctrine
rules, no gotchas/roster notes) and GREEN once the mirror lands. It pins the ticket's
completion criteria: the triage table exists, every mirrored rule artifact resolves,
and the 10 most load-bearing feedback memories are readable from the repo alone.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_MEM = _REPO / "devlab" / "runtime" / "memory"

_TRIAGE = _MEM / "notes" / "builder-memory-triage.20260708.md"
_MIRRORED_RULES = ["dispatch", "autonomy", "git-workflow", "design-doctrine"]
_NOTES = ["builder-gotchas.20260708.md", "device-roster.20260708.md"]


def _rule_files(name: str) -> list[str]:
    return glob.glob(str(_MEM / "rules" / f"cc.0.{name}.*.json"))


def test_triage_table_exists_and_covers_the_corpus():
    """The entry -> disposition -> artifact table is the deliverable."""
    assert _TRIAGE.exists(), "triage table note missing"
    text = _TRIAGE.read_text(encoding="utf-8")
    assert "Fresh-builder check" in text
    # every disposition row is one of a/b/c; the table covers the whole corpus
    rows = [l for l in text.splitlines() if l.startswith("|") and l.count("|") >= 3
            and "| a |" in l or "| b |" in l or "| c |" in l]
    assert len(rows) >= 140, f"triage table covers only {len(rows)} entries"


def test_mirrored_rule_artifacts_resolve_with_why():
    """Every category-(b) consolidation rule exists in rules/ and carries its why (CP3)."""
    for name in _MIRRORED_RULES:
        hits = _rule_files(name)
        assert hits, f"rules/{name} not materialized"
        body = json.load(open(hits[0])).get("body", {})
        assert body.get("why"), f"rules/{name} has no why"
        assert body.get("statement"), f"rules/{name} has no statement"


def test_safeguards_carries_igor_live_conditionality():
    """The no-HIGH-inertia-when-Igor-down correction reached the repo rule."""
    body = json.load(open(_rule_files("safeguards")[0]))["body"]
    assert "Igor" in body["statement"], "safeguards missing the live-cognition conditionality"


def test_top_ten_feedback_memories_readable_from_repo_alone():
    """Fresh-builder check: the load-bearing corrections resolve to repo artifacts."""
    for name in _MIRRORED_RULES + ["safeguards", "preferred_paths"]:
        assert _rule_files(name), f"rules/{name} missing"
    for note in _NOTES:
        assert (_MEM / "notes" / note).exists(), f"notes/{note} missing"
    pp = json.load(open(_rule_files("preferred_paths")[0]))["body"]
    deprecated = " ".join(e["deprecated"] for e in pp["entries"])
    assert "OpenRouter" in deprecated, "inference-proxy-only entry missing from preferred_paths"

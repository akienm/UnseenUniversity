"""Proof for T-cc-nightly-reads-decision-md (D-canonical-memory-consolidation).

cc_nightly_palace_updates scanned lab/design_docs/decisions/D-*.md — a dead path,
and decisions are JSON in devlab/runtime/memory/decisions/ now. The fix points the
scan at the JSON store and parses the envelope (body.decision_id/title/date/status/
spawned_tickets, with body.text back-filling sections). RED before (globbed D-*.md,
missed the JSON), GREEN after.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _mod():
    sys.path.insert(0, str(_REPO / "devlab" / "claudecode"))
    import cc_nightly_palace_updates as m

    return m


def test_scan_reads_json_decisions_from_store(tmp_path, monkeypatch):
    """Proof node (one intention): scan_decision_docs reads JSON decision envelopes
    from the store and parses their fields; legacy .md is not the source."""
    m = _mod()

    envelope = {
        "namespace": ["D-fixture-2026-06-23"],
        "category": "decisions",
        "body": {
            "decision_id": "D-fixture-2026-06-23",
            "title": "Fixture decision",
            "date": "2026-06-23",
            "status": "open",
            "spawned_tickets": ["T-fix-a", "T-fix-b"],
            "text": "# D-fixture\n## Decision narrative\nthe body.\n## Hypothesis\nthe claim.\n",
        },
    }
    (tmp_path / "cc.0.D-fixture-2026-06-23.20260623.000000000000.json").write_text(
        json.dumps(envelope), encoding="utf-8"
    )
    # A legacy .md in the same dir must NOT be the source (decisions are JSON now):
    (tmp_path / "D-legacy-2026-06-01.md").write_text(
        "**title:** legacy\n**date:** 2026-06-01\n", encoding="utf-8"
    )

    monkeypatch.setattr(m, "_DECISIONS_DIR", tmp_path)
    docs = m.scan_decision_docs(all_docs=True)
    slugs = {d["slug"] for d in docs}

    assert "D-fixture-2026-06-23" in slugs, f"JSON decision not scanned: {slugs}"
    assert "D-legacy-2026-06-01" not in slugs, "legacy .md should not be a decision source"

    doc = next(d for d in docs if d["slug"] == "D-fixture-2026-06-23")
    assert doc["title"] == "Fixture decision"
    assert doc["date"] == "2026-06-23"
    assert doc["spawned_tickets"] == ["T-fix-a", "T-fix-b"]
    assert doc["narrative"] == "the body."

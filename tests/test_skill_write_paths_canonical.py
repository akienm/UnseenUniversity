"""Proof for T-skill-write-paths-canonical (D-canonical-memory-consolidation).

The writing skills no longer misfile dev-process artifacts to the retired
`lab/design_docs/` paths — they emit to the canonical store
`devlab/runtime/memory/` via `memory_emit.py`. This test goes RED on the
pre-consolidation skills (which wrote a `.md` stub to `lab/design_docs/decisions/`,
projected via `migrate_one`, appended outcomes to the `.md`, and `git add`-ed
`lab/design_docs/`) and GREEN once each writer targets the canonical store.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SKILLS = _REPO / "skills"


def _skill(name: str) -> str:
    return (_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def test_sorted_emits_decision_to_canonical_store_not_lab():
    """/sorted Step 6 emits the decision JSON via memory_emit to the canonical store."""
    s = _skill("sorted")
    # Canonical write-path present (a reverted Step 6 lacks all three):
    assert "memory_emit.py" in s
    assert "--category decisions" in s
    assert "devlab/runtime/memory/decisions" in s
    # The misfiling write-paths are gone:
    assert "lab/design_docs/decisions/D-" not in s, "still writing a .md stub to lab/"
    assert "migrate_one(" not in s, "still projecting via the stale lab/claudecode migrator"
    assert "decisions_log.dsb" not in s, "still appending to the retired .dsb log"


def test_outcome_updates_the_decision_json_not_an_md_file():
    o = _skill("outcome")
    assert "lab/design_docs" not in o, "/outcome still reads/writes a lab/ .md file"
    # Design-first (T-migrate-decision-readers-to-designs): /outcome writes the
    # verdict onto the canonical DESIGN via design_emit.py (legacy-decision fallback
    # via memory_emit.py), and resolves records through the canonical resolver
    # (memory_root / iter_decision_view) — NOT a hardcoded decisions/ path literal.
    assert "design_emit.py" in o, "/outcome must write the outcome onto the canonical design"
    assert "memory_emit.py" in o, "/outcome legacy-decision fallback via the one chokepoint"
    assert "iter_decision_view" in o or "memory_root" in o, \
        "/outcome must resolve via the canonical resolver, not a hardcoded path"


def test_day_close_stages_the_canonical_store_not_lab():
    d = _skill("day-close")
    assert "git add lab/design_docs/" not in d, "day-close still stages the retired folder"
    assert "git add devlab/runtime/memory/" in d


def test_audit_skills_read_the_decision_json_not_md():
    for name in ("audit-design", "audit-hypothesis"):
        text = _skill(name)
        assert "lab/design_docs/decisions/<" not in text, f"{name} still reads a lab/ .md"
        assert "devlab/runtime/memory/decisions" in text, f"{name} must read the JSON store"


def test_no_writing_skill_misfiles_to_lab_design_docs():
    """Proof node (one intention): every decision writer/reader targets the canonical
    store, never lab/design_docs/. RED on the pre-consolidation skills, GREEN after."""
    sorted_s, outcome_s, day_s = _skill("sorted"), _skill("outcome"), _skill("day-close")
    ad, ah = _skill("audit-design"), _skill("audit-hypothesis")
    # canonical write/read present (a reverted skill lacks these)
    assert "memory_emit.py" in sorted_s and "devlab/runtime/memory/decisions" in sorted_s
    assert "memory_emit.py" in outcome_s and "devlab/runtime/memory/decisions" in outcome_s
    assert "git add devlab/runtime/memory/" in day_s
    assert "devlab/runtime/memory/decisions" in ad and "devlab/runtime/memory/decisions" in ah
    # the misfiling write/read paths are gone
    assert "lab/design_docs" not in sorted_s and "lab/design_docs" not in outcome_s
    assert "git add lab/design_docs/" not in day_s
    assert "decisions_log.dsb" not in sorted_s
    assert "lab/design_docs/decisions/<" not in ad and "lab/design_docs/decisions/<" not in ah


def test_memory_emit_writes_a_decision_to_the_canonical_store(tmp_path, monkeypatch):
    """Functional: the canonical chokepoint lands a decision JSON in decisions/ — nothing under lab/."""
    import importlib
    import sys

    # memory_emit lives in the repo's writer dir — resolve it without depending on PYTHONPATH.
    sys.path.insert(0, str(_REPO / "devlab" / "claudecode"))
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    me = importlib.import_module("memory_emit")
    importlib.reload(me)  # re-read MEMORY_ROOT from the patched env

    out = me.emit(
        "decisions", "cc.0",
        {"decision_id": "D-test-canonical", "title": "t", "status": "open",
         "date": "2026-06-23", "text": "# D-test-canonical\n## Decision narrative\nbody-text-narrative"},
        kind="decision", namespace=["D-test-canonical"],
    )
    landed = Path(out)
    assert landed.exists() and landed.parent == tmp_path / "decisions"
    assert "body-text-narrative" in landed.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("**/lab/**")), "no artifact may land under any lab/ path"

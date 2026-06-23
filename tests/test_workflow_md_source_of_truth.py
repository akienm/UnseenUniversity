"""Proof for T-workflow-md-source-of-truth.

The workflow map lives in ONE file (skills/workflow.md). `uu workflow` renders
that file verbatim, and the /workflow skill points at it instead of embedding a
(drifting) copy.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
from importlib.machinery import SourceFileLoader
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _load_uu():
    # ./uu has no .py extension — load it as a module without running main().
    loader = SourceFileLoader("uu_cli", str(_REPO / "uu"))
    spec = importlib.util.spec_from_loader("uu_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_uu_workflow_renders_the_map_from_the_single_source():
    wf = _REPO / "skills" / "workflow.md"
    assert wf.exists(), "skills/workflow.md must be the single source of truth"

    uu = _load_uu()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        uu._cmd_workflow()
    out = buf.getvalue()

    # `uu workflow` prints the source file verbatim — one source, no divergence.
    assert out.strip() == wf.read_text(encoding="utf-8").strip()

    # the rendered map is real and current (a hollow/placeholder or stale map fails here):
    for marker in ("THE TRACKING STACK", "WHERE AM I?", "/sorted", "/sprint-ticket"):
        assert marker in out, f"missing map marker: {marker}"
    assert "/decided" not in out, "stale skill name /decided must be purged from the map"


def test_skill_points_at_file_and_does_not_duplicate_the_map():
    skill = (_REPO / "skills" / "workflow" / "SKILL.md").read_text(encoding="utf-8")
    assert "skills/workflow.md" in skill, "skill must point at the source-of-truth file"
    assert "THE TRACKING STACK" not in skill, "skill must not embed a duplicate map"

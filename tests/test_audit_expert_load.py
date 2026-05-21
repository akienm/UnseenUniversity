"""
test_audit_expert_load.py — Tests for audit-expert/run expert loading + selection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Import from the run script (no .py extension — use SourceFileLoader)
import importlib.machinery
import importlib.util


def _load_run() -> object:
    run_path = str(Path(__file__).parent.parent / "skills" / "audit-expert" / "run")
    loader = importlib.machinery.SourceFileLoader("audit_expert_run", run_path)
    spec = importlib.util.spec_from_loader("audit_expert_run", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


_run = _load_run()


FIXTURE_EXPERTS_MD = """\
# Experts for TestProject

## Systems Architect
**Lens:** Is the system decomposition clean?
**Key questions:**
- Are boundaries enforced?
- What is the blast radius?

## Security Engineer
**Lens:** What can go wrong from adversarial inputs?
**Key questions:**
- Are trust boundaries enforced?
- Is the audit trail complete?

## Product Manager
**Lens:** Is the project making progress toward its goal?
**Key questions:**
- What is the velocity trend?
- Are capabilities expanding or drifting?
"""


def test_load_experts_from_experts_md(tmp_path):
    (tmp_path / "EXPERTS.md").write_text(FIXTURE_EXPERTS_MD)
    experts = _run.load_experts(str(tmp_path))
    assert len(experts) == 3
    names = [e["name"] for e in experts]
    assert "Systems Architect" in names
    assert "Security Engineer" in names
    assert "Product Manager" in names


def test_load_experts_parses_lens_and_questions(tmp_path):
    (tmp_path / "EXPERTS.md").write_text(FIXTURE_EXPERTS_MD)
    experts = _run.load_experts(str(tmp_path))
    sa = next(e for e in experts if e["name"] == "Systems Architect")
    assert "clean" in sa["lens"]
    assert len(sa["key_questions"]) == 2


def test_load_experts_fallback_when_absent(tmp_path):
    experts = _run.load_experts(str(tmp_path))
    assert experts == _run.DEFAULT_EXPERTS
    assert len(experts) == 11


def test_load_experts_fallback_when_malformed(tmp_path):
    (tmp_path / "EXPERTS.md").write_text("# Nothing here\n\nJust text.\n")
    experts = _run.load_experts(str(tmp_path))
    assert experts == _run.DEFAULT_EXPERTS


def test_select_weekly_returns_three(tmp_path):
    (tmp_path / "EXPERTS.md").write_text(FIXTURE_EXPERTS_MD)
    experts = _run.load_experts(str(tmp_path))
    selected = _run.select_experts(experts, "weekly", seed=0)
    assert len(selected) == 3
    for e in selected:
        assert e in experts


def test_select_weekly_is_deterministic(tmp_path):
    (tmp_path / "EXPERTS.md").write_text(FIXTURE_EXPERTS_MD)
    experts = _run.load_experts(str(tmp_path))
    a = _run.select_experts(experts, "weekly", seed=3)
    b = _run.select_experts(experts, "weekly", seed=3)
    assert a == b


def test_select_monthly_returns_all(tmp_path):
    (tmp_path / "EXPERTS.md").write_text(FIXTURE_EXPERTS_MD)
    experts = _run.load_experts(str(tmp_path))
    selected = _run.select_experts(experts, "monthly")
    assert selected == experts


def test_select_explicit_by_name(tmp_path):
    (tmp_path / "EXPERTS.md").write_text(FIXTURE_EXPERTS_MD)
    experts = _run.load_experts(str(tmp_path))
    selected = _run.select_experts(experts, "explicit", names=["Security"])
    assert len(selected) == 1
    assert selected[0]["name"] == "Security Engineer"


def test_select_stays_within_loaded_list(tmp_path):
    (tmp_path / "EXPERTS.md").write_text(FIXTURE_EXPERTS_MD)
    experts = _run.load_experts(str(tmp_path))
    for seed in range(7):
        selected = _run.select_experts(experts, "weekly", seed=seed)
        for e in selected:
            assert e in experts

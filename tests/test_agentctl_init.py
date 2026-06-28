"""
test_agentctl_init.py — unit tests for the env-var and shell-profile helpers
added to agentctl init in T-agentctl-env-and-skills.

Tests target the helper functions directly (not the full CLI) for isolation:
  - _write_env_var_to_profile(profile, name, value) → bool
  - _detect_cc_workflow_tools() → Path | None
  - _shell_profile() → Path
"""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from unseen_university.cli.agentctl import (
    _detect_cc_workflow_tools,
    _shell_profile,
    _write_env_var_to_profile,
)

# ── _write_env_var_to_profile ─────────────────────────────────────────────────


def test_write_env_var_creates_export_line(tmp_path: Path) -> None:
    profile = tmp_path / ".bashrc"
    written = _write_env_var_to_profile(profile, "CC_WORKFLOW_TOOLS", "/some/path")
    assert written is True
    assert "export CC_WORKFLOW_TOOLS=/some/path" in profile.read_text()


def test_write_env_var_creates_profile_if_absent(tmp_path: Path) -> None:
    profile = tmp_path / ".bashrc_new"
    assert not profile.exists()
    _write_env_var_to_profile(profile, "CC_WORKFLOW_TOOLS", "/some/path")
    assert profile.exists()
    assert "export CC_WORKFLOW_TOOLS=/some/path" in profile.read_text()


def test_write_env_var_idempotent_exact_match(tmp_path: Path) -> None:
    profile = tmp_path / ".bashrc"
    profile.write_text("export CC_WORKFLOW_TOOLS=/some/path\n")
    written = _write_env_var_to_profile(profile, "CC_WORKFLOW_TOOLS", "/some/path")
    assert written is False
    # File should not have a duplicate line
    lines = [
        ln
        for ln in profile.read_text().splitlines()
        if "export CC_WORKFLOW_TOOLS=" in ln
    ]
    assert len(lines) == 1


def test_write_env_var_idempotent_different_value(tmp_path: Path) -> None:
    """If the var is already exported (even with a different value), do not overwrite."""
    profile = tmp_path / ".bashrc"
    profile.write_text("export CC_WORKFLOW_TOOLS=/old/path\n")
    written = _write_env_var_to_profile(profile, "CC_WORKFLOW_TOOLS", "/new/path")
    assert written is False
    assert "export CC_WORKFLOW_TOOLS=/old/path" in profile.read_text()
    assert "/new/path" not in profile.read_text()


def test_write_env_var_comment_does_not_fool_idempotency(tmp_path: Path) -> None:
    """A comment mentioning the var should not prevent the real export being added."""
    profile = tmp_path / ".bashrc"
    profile.write_text("# CC_WORKFLOW_TOOLS is documented here\n")
    written = _write_env_var_to_profile(profile, "CC_WORKFLOW_TOOLS", "/some/path")
    assert written is True
    assert "export CC_WORKFLOW_TOOLS=/some/path" in profile.read_text()


def test_write_env_var_preserves_existing_content(tmp_path: Path) -> None:
    profile = tmp_path / ".bashrc"
    profile.write_text("export EXISTING_VAR=1\n")
    _write_env_var_to_profile(profile, "CC_WORKFLOW_TOOLS", "/some/path")
    text = profile.read_text()
    assert "export EXISTING_VAR=1" in text
    assert "export CC_WORKFLOW_TOOLS=/some/path" in text


# ── _detect_cc_workflow_tools ─────────────────────────────────────────────────


def test_detect_cc_workflow_tools_returns_path_or_none() -> None:
    """Smoke test: returns a Path if cc_queue.py exists there, else None."""
    result = _detect_cc_workflow_tools()
    # On this machine devlab/claudecode is in TheIgors, not in unseen_university,
    # so the function should gracefully return None.
    assert result is None or isinstance(result, Path)


def test_detect_cc_workflow_tools_path_has_cc_queue_when_found() -> None:
    """If a Path is returned, cc_queue.py must exist at that location."""
    result = _detect_cc_workflow_tools()
    if result is not None:
        assert (
            result / "cc_queue.py"
        ).exists(), (
            f"_detect_cc_workflow_tools returned {result} but cc_queue.py missing there"
        )


def test_detect_cc_workflow_tools_respects_fake_repo(
    tmp_path: Path, monkeypatch
) -> None:
    """When DEFAULT_MASTER_ROOT points into a tree that has devlab/claudecode/cc_queue.py,
    the function returns that path."""
    import unseen_university.devices.installer.shim as shim_mod

    # Build a fake repo tree: tmp/skills/ and tmp/devlab/claudecode/cc_queue.py
    fake_skills = tmp_path / "skills"
    fake_skills.mkdir()
    fake_claudecode = tmp_path / "devlab" / "claudecode"
    fake_claudecode.mkdir(parents=True)
    (fake_claudecode / "cc_queue.py").write_text("# fake cc_queue\n")

    monkeypatch.setattr(shim_mod, "DEFAULT_MASTER_ROOT", fake_skills)

    result = _detect_cc_workflow_tools()
    assert result == fake_claudecode


def test_detect_cc_workflow_tools_missing_cc_queue_returns_none(
    tmp_path: Path, monkeypatch
) -> None:
    """If devlab/claudecode exists but cc_queue.py is absent, return None."""
    import unseen_university.devices.installer.shim as shim_mod

    fake_skills = tmp_path / "skills"
    fake_skills.mkdir()
    fake_claudecode = tmp_path / "devlab" / "claudecode"
    fake_claudecode.mkdir(parents=True)
    # cc_queue.py deliberately NOT created

    monkeypatch.setattr(shim_mod, "DEFAULT_MASTER_ROOT", fake_skills)

    result = _detect_cc_workflow_tools()
    assert result is None


# ── _shell_profile ────────────────────────────────────────────────────────────


def test_shell_profile_returns_zshrc_on_darwin(monkeypatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    p = _shell_profile()
    assert p.name == ".zshrc"
    assert p.parent == Path.home()


def test_shell_profile_returns_bashrc_on_linux(monkeypatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    p = _shell_profile()
    assert p.name == ".bashrc"
    assert p.parent == Path.home()


def test_shell_profile_returns_powershell_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    p = _shell_profile()
    assert p.name == "Microsoft.PowerShell_profile.ps1"

"""
test_skill_deploy.py — installer/shim.py + backends.py regression tests.

Covers:
  - deploy is idempotent (deploy twice → same result, no churn)
  - manifest-unlisted skills in target are LEFT ALONE (preserves user-added)
  - manifest-listed but missing-source skills are reported, not crashed on
  - per-host filtering: skill listed for other hosts is skipped here
  - WindowsBackend.deploy_skill raises NotImplementedError (stub safety)
  - select_backend picks RsyncBackend on non-Windows
"""

from __future__ import annotations

import json
import platform
from pathlib import Path

import pytest

from devices.installer import deploy_skills, deploy_status
from devices.installer.backends import (
    RsyncBackend,
    WindowsBackend,
    select_backend,
)
from devices.installer.manifest import load_manifest


@pytest.fixture
def fake_skill_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a minimal master/skills tree, manifest, and empty target."""
    master = tmp_path / "master_skills"
    master.mkdir()
    (master / "alpha").mkdir()
    (master / "alpha" / "SKILL.md").write_text("# alpha skill\n")
    (master / "beta").mkdir()
    (master / "beta" / "SKILL.md").write_text("# beta skill\n")
    (master / "gamma_other_host").mkdir()
    (master / "gamma_other_host" / "SKILL.md").write_text("# gamma\n")

    manifest = master / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "alpha": {
                        "category": "machine-agnostic",
                        "machines": ["*"],
                        "deploy": True,
                    },
                    "beta": {
                        "category": "machine-agnostic",
                        "machines": ["*"],
                        "deploy": True,
                    },
                    "gamma_other_host": {
                        "category": "machine-agnostic",
                        "machines": ["definitely-not-this-host"],
                        "deploy": True,
                    },
                    "missing_source": {
                        "category": "machine-agnostic",
                        "machines": ["*"],
                        "deploy": True,
                    },
                    "disabled_one": {
                        "category": "machine-agnostic",
                        "machines": ["*"],
                        "deploy": False,
                    },
                },
            }
        )
    )

    target = tmp_path / "claude_skills"
    target.mkdir()
    return master, manifest, target


def test_deploy_idempotent(fake_skill_tree):
    master, manifest, target = fake_skill_tree

    first = deploy_skills(master_root=master, target=target, manifest_path=manifest)
    second = deploy_skills(master_root=master, target=target, manifest_path=manifest)

    assert first.deployed == second.deployed == ["alpha", "beta"]
    assert (target / "alpha" / "SKILL.md").read_text() == "# alpha skill\n"
    assert (target / "beta" / "SKILL.md").read_text() == "# beta skill\n"


def test_deploy_preserves_user_added_local_skills(fake_skill_tree):
    master, manifest, target = fake_skill_tree
    (target / "my_local_skill").mkdir()
    (target / "my_local_skill" / "SKILL.md").write_text("# my custom skill\n")

    result = deploy_skills(master_root=master, target=target, manifest_path=manifest)

    assert "my_local_skill" in result.untouched_local
    assert (target / "my_local_skill" / "SKILL.md").read_text() == "# my custom skill\n"


def test_deploy_skips_other_host_entries(fake_skill_tree):
    master, manifest, target = fake_skill_tree
    result = deploy_skills(master_root=master, target=target, manifest_path=manifest)
    assert "gamma_other_host" in result.skipped_not_for_host
    assert not (target / "gamma_other_host").exists()


def test_deploy_reports_missing_source_does_not_crash(fake_skill_tree):
    master, manifest, target = fake_skill_tree
    result = deploy_skills(master_root=master, target=target, manifest_path=manifest)
    assert "missing_source" in result.skipped_missing_source


def test_deploy_skips_disabled_skills(fake_skill_tree):
    master, manifest, target = fake_skill_tree
    (master / "disabled_one").mkdir()
    (master / "disabled_one" / "SKILL.md").write_text("# disabled\n")
    result = deploy_skills(master_root=master, target=target, manifest_path=manifest)
    assert "disabled_one" in result.skipped_disabled
    assert not (target / "disabled_one").exists()


def test_deploy_overwrites_stale_target_content(fake_skill_tree):
    master, manifest, target = fake_skill_tree
    (target / "alpha").mkdir()
    (target / "alpha" / "SKILL.md").write_text("# OLD CONTENT\n")
    (target / "alpha" / "stale_extra.md").write_text("# should be removed\n")

    deploy_skills(master_root=master, target=target, manifest_path=manifest)

    assert (target / "alpha" / "SKILL.md").read_text() == "# alpha skill\n"
    assert not (target / "alpha" / "stale_extra.md").exists()


def test_deploy_status_shape(fake_skill_tree):
    master, manifest, target = fake_skill_tree
    (target / "my_local_skill").mkdir()

    info = deploy_status(master_root=master, target=target, manifest_path=manifest)

    assert info["host"]
    assert "alpha" in info["managed_for_host"]
    assert "beta" in info["managed_for_host"]
    assert "gamma_other_host" not in info["managed_for_host"]
    assert "my_local_skill" in info["local_only"]


def test_windows_backend_deploy_raises_on_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError, match="source skill dir missing"):
        WindowsBackend().deploy_skill(tmp_path / "nonexistent", tmp_path / "dst")


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="non-Windows hosts only",
)
def test_select_backend_picks_rsync_on_posix():
    backend = select_backend()
    assert isinstance(backend, RsyncBackend)


def test_load_manifest_rejects_unknown_version(tmp_path):
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps({"version": 999, "skills": {}}))
    with pytest.raises(ValueError, match="version 999 unsupported"):
        load_manifest(bad)

"""
Unit tests for the Installer: deploy_skills(), manifest loader, and backends.

InstallerDevice.device.py is currently empty — these tests cover the shim
(deploy_skills), manifest (load_manifest / SkillEntry.deploys_here), and
backends (RsyncBackend, select_backend).
"""

from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devices.installer.manifest import SkillEntry, load_manifest
from devices.installer.shim import deploy_skills, deploy_status

# ── Manifest ──────────────────────────────────────────────────────────────────


@pytest.fixture
def manifest_path(tmp_path):
    data = {
        "version": 1,
        "skills": {
            "sprint": {
                "category": "machine-agnostic",
                "machines": ["*"],
                "deploy": True,
            },
            "igor-diagnose": {
                "category": "igor-specific",
                "machines": ["akiendelllinux"],
                "deploy": True,
            },
            "disabled-skill": {
                "category": "machine-agnostic",
                "machines": ["*"],
                "deploy": False,
            },
        },
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data))
    return p


def test_load_manifest_returns_skill_entries(manifest_path):
    skills = load_manifest(manifest_path)
    assert "sprint" in skills
    assert isinstance(skills["sprint"], SkillEntry)


def test_load_manifest_all_keys_present(manifest_path):
    skills = load_manifest(manifest_path)
    entry = skills["sprint"]
    assert entry.name == "sprint"
    assert entry.category == "machine-agnostic"
    assert entry.deploy is True
    assert "*" in entry.machines


def test_load_manifest_wrong_version_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"version": 99, "skills": {}}))
    with pytest.raises(ValueError, match="unsupported"):
        load_manifest(p)


def test_skill_entry_deploys_here_wildcard():
    entry = SkillEntry("sprint", "machine-agnostic", ["*"], True)
    assert entry.deploys_here("any-host") is True


def test_skill_entry_does_not_deploy_when_disabled():
    entry = SkillEntry("sprint", "machine-agnostic", ["*"], False)
    assert entry.deploys_here("any-host") is False


def test_skill_entry_deploys_here_specific_host():
    entry = SkillEntry("igor-diag", "igor-specific", ["igor-host"], True)
    assert entry.deploys_here("igor-host") is True
    assert entry.deploys_here("other-host") is False


# ── deploy_skills() ───────────────────────────────────────────────────────────


@pytest.fixture
def master_root(tmp_path):
    root = tmp_path / "master"
    root.mkdir()
    # Create one skill directory
    (root / "sprint").mkdir()
    (root / "sprint" / "skill.md").write_text("# sprint skill")
    return root


@pytest.fixture
def deploy_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    return target


@pytest.fixture
def simple_manifest(tmp_path):
    data = {
        "version": 1,
        "skills": {
            "sprint": {
                "category": "machine-agnostic",
                "machines": ["*"],
                "deploy": True,
            },
            "missing-skill": {
                "category": "machine-agnostic",
                "machines": ["*"],
                "deploy": True,
            },
            "disabled": {
                "category": "machine-agnostic",
                "machines": ["*"],
                "deploy": False,
            },
        },
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data))
    return p


def test_deploy_skills_deploys_present_skill(
    master_root, deploy_target, simple_manifest
):
    backend = MagicMock()
    result = deploy_skills(
        master_root=master_root,
        target=deploy_target,
        manifest_path=simple_manifest,
        hostname="test-host",
        backend=backend,
    )
    assert "sprint" in result.deployed
    assert backend.deploy_skill.called


def test_deploy_skills_skips_disabled(master_root, deploy_target, simple_manifest):
    backend = MagicMock()
    result = deploy_skills(
        master_root=master_root,
        target=deploy_target,
        manifest_path=simple_manifest,
        hostname="test-host",
        backend=backend,
    )
    assert "disabled" in result.skipped_disabled


def test_deploy_skills_skips_missing_source(
    master_root, deploy_target, simple_manifest
):
    backend = MagicMock()
    result = deploy_skills(
        master_root=master_root,
        target=deploy_target,
        manifest_path=simple_manifest,
        hostname="test-host",
        backend=backend,
    )
    assert "missing-skill" in result.skipped_missing_source


def test_deploy_skills_creates_target_dir(tmp_path, master_root, simple_manifest):
    target = tmp_path / "new_target"
    backend = MagicMock()
    deploy_skills(
        master_root=master_root,
        target=target,
        manifest_path=simple_manifest,
        hostname="test-host",
        backend=backend,
    )
    assert target.exists()


def test_deploy_skills_preserves_local_only_skills(
    master_root, deploy_target, simple_manifest
):
    (deploy_target / "local-skill").mkdir()
    backend = MagicMock()
    result = deploy_skills(
        master_root=master_root,
        target=deploy_target,
        manifest_path=simple_manifest,
        hostname="test-host",
        backend=backend,
    )
    assert "local-skill" in result.untouched_local


def test_deploy_skills_skips_not_for_host(master_root, deploy_target, tmp_path):
    data = {
        "version": 1,
        "skills": {
            "igor-diag": {
                "category": "igor-specific",
                "machines": ["igor-machine"],
                "deploy": True,
            }
        },
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data))
    (master_root / "igor-diag").mkdir()

    backend = MagicMock()
    result = deploy_skills(
        master_root=master_root,
        target=deploy_target,
        manifest_path=manifest,
        hostname="other-machine",
        backend=backend,
    )
    assert "igor-diag" in result.skipped_not_for_host
    assert not backend.deploy_skill.called


# ── deploy_status() ──────────────────────────────────────────────────────────


def test_deploy_status_reports_managed_for_host(
    master_root, deploy_target, simple_manifest
):
    status = deploy_status(
        master_root=master_root,
        target=deploy_target,
        manifest_path=simple_manifest,
        hostname="test-host",
    )
    assert "sprint" in status["managed_for_host"]
    assert "host" in status
    assert "target" in status


def test_deploy_status_reports_missing_in_target(
    master_root, deploy_target, simple_manifest
):
    status = deploy_status(
        master_root=master_root,
        target=deploy_target,
        manifest_path=simple_manifest,
        hostname="test-host",
    )
    # sprint is managed but not yet deployed → missing_in_target
    assert "sprint" in status["missing_in_target"]


# ── RsyncBackend ──────────────────────────────────────────────────────────────


@pytest.mark.skipif(not shutil.which("rsync"), reason="rsync not available")
class TestRsyncBackend:
    def test_is_available_when_rsync_present(self):
        from devices.installer.backends import RsyncBackend

        assert RsyncBackend().is_available() is True

    def test_deploy_skill_mirrors_contents(self, tmp_path):
        from devices.installer.backends import RsyncBackend

        src = tmp_path / "src_skill"
        src.mkdir()
        (src / "skill.md").write_text("# test skill")
        dst = tmp_path / "dst_skill"

        RsyncBackend().deploy_skill(src, dst)
        assert (dst / "skill.md").exists()

    def test_deploy_skill_raises_on_missing_source(self, tmp_path):
        from devices.installer.backends import RsyncBackend

        with pytest.raises(FileNotFoundError):
            RsyncBackend().deploy_skill(tmp_path / "nonexistent", tmp_path / "dst")


# ── select_backend ────────────────────────────────────────────────────────────


def test_select_backend_returns_backend():
    from devices.installer.backends import select_backend

    backend = select_backend()
    assert backend.is_available() is True


@pytest.mark.skipif(platform.system() == "Windows", reason="Linux/Mac only")
def test_select_backend_is_rsync_on_linux_mac():
    from devices.installer.backends import RsyncBackend, select_backend

    if shutil.which("rsync"):
        assert isinstance(select_backend(), RsyncBackend)

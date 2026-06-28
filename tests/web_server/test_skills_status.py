"""
Smoke test: agentctl skills status CLI command.

Verifies that `agentctl skills status` exits 0 and prints a managed-skills
summary with at least one entry. Uses a patched deploy_status so the test
doesn't depend on the local ~/.claude/skills tree.
"""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from unseen_university.cli.agentctl import cli

_FAKE_STATUS = {
    "host": "test-host",
    "manifest_path": "/fake/skills/manifest.json",
    "target": "/fake/.claude/skills",
    "managed_for_host": ["alpha", "beta"],
    "present_in_target": ["alpha", "beta"],
    "local_only": [],
    "missing_in_target": [],
}


def test_skills_status_exits_zero():
    runner = CliRunner()
    with patch("unseen_university.devices.installer.deploy_status", return_value=_FAKE_STATUS):
        result = runner.invoke(cli, ["skills", "status"])
    assert result.exit_code == 0, result.output


def test_skills_status_lists_managed_count():
    runner = CliRunner()
    with patch("unseen_university.devices.installer.deploy_status", return_value=_FAKE_STATUS):
        result = runner.invoke(cli, ["skills", "status"])
    assert "managed:" in result.output
    assert "2" in result.output


def test_skills_status_shows_host_and_target():
    runner = CliRunner()
    with patch("unseen_university.devices.installer.deploy_status", return_value=_FAKE_STATUS):
        result = runner.invoke(cli, ["skills", "status"])
    assert "host:" in result.output
    assert "target:" in result.output

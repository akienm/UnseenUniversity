"""
Tests for AuditorDevice.

Unit tests mock the DB and subprocess calls to stay fast and isolated.
Integration tests require IGOR_HOME_DB_URL and a live Postgres instance.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devices.auditor.device import AuditorDevice, CHECKS_PATH, _SEVERITY_ORDER

SAMPLE_CHECKS = {
    "forever": [
        {
            "name": "no-sqlite-imports",
            "code": "AR-003",
            "kind": "shell",
            "pattern": "python3 lab/claudecode/audit_check_sqlite_imports.py",
            "description": "No SQLite imports in Igor data path",
            "severity": "high",
            "added_by": "test",
            "ack_until": "2026-01-01T00:00:00+00:00",
            "mode": "forever",
        },
        {
            "name": "no-bare-except-pass",
            "code": "AR-001",
            "kind": "shell",
            "pattern": "python3 lab/claudecode/audit_check_bare_except.py",
            "description": "Bare except: pass blocks",
            "severity": "med",
            "added_by": "test",
            "ack_until": None,
            "mode": "forever",
        },
        {
            "name": "low-severity-check",
            "code": "AR-099",
            "kind": "shell",
            "pattern": "echo ok",
            "description": "A low severity check",
            "severity": "low",
            "added_by": "test",
            "ack_until": None,
            "mode": "forever",
        },
    ],
    "next_sweep": [],
    "history": [],
}


@pytest.fixture
def device(tmp_path):
    """AuditorDevice with DB mocked out and a temp checks file."""
    checks_file = tmp_path / "audit_checks.json"
    checks_file.write_text(json.dumps(SAMPLE_CHECKS))
    with (
        patch("devices.auditor.device._db_conn", MagicMock()),
        patch("devices.auditor.device.CHECKS_PATH", checks_file),
    ):
        yield AuditorDevice()


class TestSeverityOrder:
    def test_high_lower_than_med(self):
        assert _SEVERITY_ORDER["high"] < _SEVERITY_ORDER["med"]

    def test_med_lower_than_low(self):
        assert _SEVERITY_ORDER["med"] < _SEVERITY_ORDER["low"]


class TestAuditorDeviceContract:
    def test_who_am_i(self, device):
        info = device.who_am_i()
        assert info["device_id"] == "auditor"
        assert info["name"]
        assert info["version"]

    def test_interface_version(self, device):
        assert "1.0" in device.interface_version() or device.interface_version()

    def test_mcp_tools_in_capabilities(self, device):
        caps = device.capabilities()
        assert "run_check" in caps["mcp_tools"]
        assert "run_all" in caps["mcp_tools"]
        assert "finding_history" in caps["mcp_tools"]

    def test_psycopg2_in_requirements(self, device):
        assert "psycopg2" in device.requirements()["deps"]

    def test_uptime_increases(self, device):
        import time

        time.sleep(0.01)
        assert device.uptime() > 0

    def test_health_returns_healthy(self, device):
        result = device.health()
        assert result["status"] == "healthy"
        assert result["check_count"]["forever"] == len(SAMPLE_CHECKS["forever"])


class TestCheckList:
    def test_check_list_returns_forever_checks(self, device):
        result = device.check_list()
        names = [c["name"] for c in result["forever"]]
        assert "no-sqlite-imports" in names
        assert "no-bare-except-pass" in names

    def test_check_list_has_next_sweep_key(self, tmp_path):
        checks_with_next = {
            **SAMPLE_CHECKS,
            "next_sweep": [
                {
                    "name": "one-shot",
                    "kind": "shell",
                    "pattern": "echo next",
                    "severity": "low",
                    "description": "next_sweep check",
                    "ack_until": None,
                    "mode": "next",
                }
            ],
        }
        checks_file = tmp_path / "audit_checks.json"
        checks_file.write_text(json.dumps(checks_with_next))
        with (
            patch("devices.auditor.device._db_conn", MagicMock()),
            patch("devices.auditor.device.CHECKS_PATH", checks_file),
        ):
            d = AuditorDevice()
            result = d.check_list()
        assert "next_sweep" in result
        assert any(c["name"] == "one-shot" for c in result["next_sweep"])


class TestRunCheck:
    def test_nonexistent_check_returns_error(self, device):
        result = device.run_check("nonexistent-check")
        assert len(result) == 1
        assert result[0]["status"] == "ERROR"
        assert "check not found" in result[0]["detail"]

    def test_run_always_pass(self, tmp_path):
        checks = {
            "forever": [
                {
                    "name": "always-pass",
                    "kind": "shell",
                    "pattern": "exit 0",
                    "severity": "low",
                    "description": "always exits 0",
                    "ack_until": None,
                    "mode": "forever",
                }
            ],
            "next_sweep": [],
            "history": [],
        }
        checks_file = tmp_path / "audit_checks.json"
        checks_file.write_text(json.dumps(checks))
        with (
            patch("devices.auditor.device._db_conn", MagicMock()),
            patch("devices.auditor.device.CHECKS_PATH", checks_file),
        ):
            d = AuditorDevice()
            result = d.run_check("always-pass")
        assert result[0]["status"] == "PASS"

    def test_run_always_fail(self, tmp_path):
        checks = {
            "forever": [
                {
                    "name": "always-fail",
                    "kind": "shell",
                    "pattern": "exit 1",
                    "severity": "med",
                    "description": "always exits non-zero",
                    "ack_until": None,
                    "mode": "forever",
                }
            ],
            "next_sweep": [],
            "history": [],
        }
        checks_file = tmp_path / "audit_checks.json"
        checks_file.write_text(json.dumps(checks))
        with (
            patch("devices.auditor.device._db_conn", MagicMock()),
            patch("devices.auditor.device.CHECKS_PATH", checks_file),
        ):
            d = AuditorDevice()
            result = d.run_check("always-fail")
        assert result[0]["status"] == "FAIL"

    def test_run_silenced_check(self, tmp_path):
        checks = {
            "forever": [
                {
                    "name": "silenced-check",
                    "kind": "shell",
                    "pattern": "exit 1",
                    "severity": "high",
                    "description": "silenced",
                    "ack_until": "2099-12-31",
                    "mode": "forever",
                }
            ],
            "next_sweep": [],
            "history": [],
        }
        checks_file = tmp_path / "audit_checks.json"
        checks_file.write_text(json.dumps(checks))
        with (
            patch("devices.auditor.device._db_conn", MagicMock()),
            patch("devices.auditor.device.CHECKS_PATH", checks_file),
        ):
            d = AuditorDevice()
            result = d.run_check("silenced-check")
        assert result[0]["status"] == "ACKED"

    def test_finding_has_required_fields(self, tmp_path):
        checks = {
            "forever": [
                {
                    "name": "key-check",
                    "kind": "shell",
                    "pattern": "echo key test",
                    "severity": "med",
                    "description": "key test",
                    "ack_until": None,
                    "mode": "forever",
                }
            ],
            "next_sweep": [],
            "history": [],
        }
        checks_file = tmp_path / "audit_checks.json"
        checks_file.write_text(json.dumps(checks))
        with (
            patch("devices.auditor.device._db_conn", MagicMock()),
            patch("devices.auditor.device.CHECKS_PATH", checks_file),
        ):
            d = AuditorDevice()
            result = d.run_check("key-check")
        finding = result[0]
        assert "status" in finding
        assert "detail" in finding
        assert "severity" in finding


class TestRunAll:
    def test_med_excludes_low(self, device):
        results = device.run_all(severity_min="med")
        names = [r["name"] for r in results]
        assert "low-severity-check" not in names
        assert "no-sqlite-imports" in names

    def test_low_includes_all(self, device):
        results = device.run_all(severity_min="low")
        names = [r["name"] for r in results]
        assert "no-sqlite-imports" in names
        assert "no-bare-except-pass" in names
        assert "low-severity-check" in names

    def test_high_excludes_med_and_low(self, device):
        results = device.run_all(severity_min="high")
        names = [r["name"] for r in results]
        assert "no-sqlite-imports" in names
        assert "no-bare-except-pass" not in names
        assert "low-severity-check" not in names


class TestCheckAdd:
    def test_adds_new_check(self, device, tmp_path):
        checks_file = tmp_path / "audit_checks.json"
        checks_file.write_text(json.dumps(SAMPLE_CHECKS))
        with (
            patch("devices.auditor.device._db_conn", MagicMock()),
            patch("devices.auditor.device.CHECKS_PATH", checks_file),
        ):
            d = AuditorDevice()
            result = d.check_add(
                name="new-check",
                kind="shell",
                pattern="echo hello",
                severity="low",
                description="test addition",
            )
        assert result["status"] == "ok"
        saved = json.loads(checks_file.read_text())
        names = [c["name"] for c in saved["forever"]]
        assert "new-check" in names

    def test_rejects_duplicate_name(self, device, tmp_path):
        checks_file = tmp_path / "audit_checks.json"
        checks_file.write_text(json.dumps(SAMPLE_CHECKS))
        with (
            patch("devices.auditor.device._db_conn", MagicMock()),
            patch("devices.auditor.device.CHECKS_PATH", checks_file),
        ):
            d = AuditorDevice()
            result = d.check_add(
                name="no-sqlite-imports",
                kind="shell",
                pattern="echo",
                severity="med",
                description="duplicate",
            )
        assert result["status"] == "error"
        assert "already exists" in result["detail"]


_PG_URL = os.environ.get("IGOR_HOME_DB_URL", "")
_skip_integration = pytest.mark.skipif(
    not _PG_URL, reason="IGOR_HOME_DB_URL not set — skipping integration tests"
)


class TestAuditorIntegration:
    @pytest.fixture
    def live_device(self):
        return AuditorDevice()

    @_skip_integration
    def test_health_healthy(self, live_device):
        assert live_device.health()["status"] == "healthy"

    @_skip_integration
    def test_check_list_returns_list(self, live_device):
        checks = live_device.check_list()
        assert isinstance(checks, dict)
        assert len(checks.get("forever", [])) > 0

    @_skip_integration
    def test_run_check_no_sqlite_imports(self, live_device):
        results = live_device.run_check("no-sqlite-imports")
        assert len(results) == 1
        assert results[0]["name"] == "no-sqlite-imports"
        assert results[0]["status"] in ("PASS", "FAIL", "ACKED")

    @_skip_integration
    def test_finding_history_returns_list(self, live_device):
        history = live_device.finding_history(days=1)
        assert isinstance(history, list)

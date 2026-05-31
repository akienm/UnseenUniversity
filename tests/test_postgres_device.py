"""Unit tests for devices/postgres/device.py — PostgresDevice.

Integration tests (real Postgres) are skipped when AGENT_DATACENTER_DB_URL
is absent so the suite stays green on a fresh checkout with no DB.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from devices.postgres.device import PostgresDevice

# ── Contract shape ────────────────────────────────────────────────────────────


def test_who_am_i_shape():
    dev = PostgresDevice()
    info = dev.who_am_i()
    assert info["device_id"] == "postgres"
    assert "name" in info
    assert "version" in info
    assert "purpose" in info


def test_capabilities_shape():
    dev = PostgresDevice()
    caps = dev.capabilities()
    assert caps["query"] is True
    assert caps["write"] is True


def test_comms_shape():
    dev = PostgresDevice()
    c = dev.comms()
    assert c["address"] == "comms://postgres/inbox"
    assert c["mode"] == "read_write"


def test_startup_errors_is_list():
    dev = PostgresDevice()
    assert isinstance(dev.startup_errors(), list)


def test_requirements_shape():
    dev = PostgresDevice()
    req = dev.requirements()
    assert "port" in req
    assert "deps" in req


def test_update_info_shape():
    dev = PostgresDevice()
    info = dev.update_info()
    assert "current_version" in info
    assert "update_available" in info


def test_logs_shape():
    dev = PostgresDevice()
    logs = dev.logs()
    assert "paths" in logs


# ── health() with no DB ───────────────────────────────────────────────────────


def test_health_unhealthy_when_no_db_url(monkeypatch):
    monkeypatch.delenv("AGENT_DATACENTER_DB_URL", raising=False)
    monkeypatch.delenv("AGENT_DATACENTER_POSTGRES_URL", raising=False)
    dev = PostgresDevice()
    h = dev.health()
    assert h["status"] == "unhealthy"
    assert h["connected"] is False
    assert "checked_at" in h


def test_health_unhealthy_when_connection_refused(monkeypatch):
    monkeypatch.setenv("AGENT_DATACENTER_DB_URL", "postgresql://localhost:1/noexist")
    dev = PostgresDevice()
    h = dev.health()
    assert h["status"] in ("unhealthy", "degraded")
    assert "checked_at" in h


# ── blocked registry guard ────────────────────────────────────────────────────


def test_health_raises_when_blocked_in_registry():
    from unseen_university.skeleton.exceptions import DeviceBlockedError

    mock_registry = MagicMock()
    mock_registry.get_device.return_value = {
        "status": "blocked",
        "block_type": "manual",
        "blocked_since": "2026-01-01T00:00:00+00:00",
    }
    dev = PostgresDevice(registry=mock_registry)
    with pytest.raises(DeviceBlockedError):
        dev.health()


def test_health_ok_when_not_blocked_in_registry(monkeypatch):
    monkeypatch.delenv("AGENT_DATACENTER_DB_URL", raising=False)
    monkeypatch.delenv("AGENT_DATACENTER_POSTGRES_URL", raising=False)
    mock_registry = MagicMock()
    mock_registry.get_device.return_value = {"status": "running"}
    dev = PostgresDevice(registry=mock_registry)
    h = dev.health()
    assert h["status"] == "unhealthy"  # no DB → unhealthy, but no exception


# ── uptime / restart / halt / recovery with no shim ──────────────────────────


def test_uptime_zero_when_no_db(monkeypatch):
    monkeypatch.delenv("AGENT_DATACENTER_DB_URL", raising=False)
    monkeypatch.delenv("AGENT_DATACENTER_POSTGRES_URL", raising=False)
    dev = PostgresDevice()
    assert dev.uptime() == 0.0


def test_restart_noop_without_shim():
    dev = PostgresDevice()
    dev.restart()  # must not raise


def test_block_noop_without_shim():
    dev = PostgresDevice()
    dev.block("maintenance")  # must not raise


def test_halt_noop_without_shim():
    dev = PostgresDevice()
    dev.halt()  # must not raise


def test_recovery_noop_without_shim():
    dev = PostgresDevice()
    dev.recovery()  # must not raise


# ── shim delegation ───────────────────────────────────────────────────────────


def test_restart_delegates_to_shim():
    from devices.postgres.shim import PostgresShim

    mock_shim = MagicMock(spec=PostgresShim)
    dev = PostgresDevice(shim=mock_shim)
    dev.restart()
    mock_shim.restart.assert_called_once()


def test_halt_delegates_to_shim():
    from devices.postgres.shim import PostgresShim

    mock_shim = MagicMock(spec=PostgresShim)
    dev = PostgresDevice(shim=mock_shim)
    dev.halt()
    mock_shim.stop.assert_called_once()


def test_recovery_delegates_to_shim():
    from devices.postgres.shim import PostgresShim

    mock_shim = MagicMock(spec=PostgresShim)
    dev = PostgresDevice(shim=mock_shim)
    dev.recovery()
    mock_shim.start.assert_called_once()


# ── integration: real Postgres ────────────────────────────────────────────────

_DB_AVAILABLE = bool(os.environ.get("AGENT_DATACENTER_DB_URL"))


@pytest.mark.skipif(not _DB_AVAILABLE, reason="AGENT_DATACENTER_DB_URL not set")
class TestPostgresIntegration:
    def test_health_healthy(self):
        dev = PostgresDevice()
        h = dev.health()
        assert h["status"] == "healthy"
        assert h["connected"] is True
        assert h["query_latency_ms"] is not None

    def test_uptime_positive(self):
        dev = PostgresDevice()
        assert dev.uptime() > 0

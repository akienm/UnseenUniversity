"""
Unit tests for PostgresDevice.

DB-dependent methods (health, uptime, logs, where_and_how) are tested
with a mocked connection so they run on a fresh checkout without Postgres.
Integration tests that hit real Postgres are skipped unless UU_HOME_DB_URL
is set — the coverage environment has the DB and those paths count.
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from devices.postgres.device import PostgresDevice
from unseen_university.device import INTERFACE_VERSION
from unseen_university.skeleton.exceptions import DeviceBlockedError

_PG_URL = (
    os.environ.get("AGENT_DATACENTER_DB_URL")
    or os.environ.get("AGENT_DATACENTER_POSTGRES_URL")
    or ""
)
_skip_no_db = pytest.mark.skipif(not _PG_URL, reason="AGENT_DATACENTER_DB_URL not set")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def device():
    return PostgresDevice()


@pytest.fixture
def device_with_registry():
    registry = MagicMock()
    registry.get_device.return_value = None  # not blocked
    return PostgresDevice(registry=registry)


# ── BaseDevice contract (no DB) ───────────────────────────────────────────────


def test_who_am_i_required_keys(device):
    info = device.who_am_i()
    assert info["device_id"] == "postgres"
    assert "name" in info
    assert "version" in info


def test_requirements_has_deps(device):
    reqs = device.requirements()
    assert "deps" in reqs
    assert "psycopg2" in reqs["deps"]


def test_capabilities_has_required_keys(device):
    caps = device.capabilities()
    for key in ("can_send", "can_receive", "emitted_keywords"):
        assert key in caps


def test_comms_has_required_keys(device):
    c = device.comms()
    for key in ("address", "mode", "supports_push", "supports_pull", "supports_nudge"):
        assert key in c


def test_comms_address_starts_with_comms(device):
    assert device.comms()["address"].startswith("comms://")


def test_interface_version(device):
    assert device.interface_version() == INTERFACE_VERSION


def test_startup_errors_is_list(device):
    assert isinstance(device.startup_errors(), list)


def test_logs_has_paths_key(device):
    with patch("devices.postgres.device._pg_connect", return_value=None):
        logs = device.logs()
    assert "paths" in logs


def test_update_info_has_required_keys(device):
    info = device.update_info()
    assert "current_version" in info
    assert "update_available" in info


def test_where_and_how_has_required_keys(device):
    with patch("devices.postgres.device._pg_connect", return_value=None):
        w = device.where_and_how()
    for key in ("host", "pid", "launch_command"):
        assert key in w


def test_restart_delegates_to_shim(device):
    shim = MagicMock()
    device._shim = shim
    device.restart()
    shim.restart.assert_called_once()


def test_block_stops_shim(device):
    shim = MagicMock()
    device._shim = shim
    device.block("test reason")
    shim.stop.assert_called_once()


def test_halt_stops_shim(device):
    shim = MagicMock()
    device._shim = shim
    device.halt()
    shim.stop.assert_called_once()


def test_recovery_starts_shim(device):
    shim = MagicMock()
    device._shim = shim
    device.recovery()
    shim.start.assert_called_once()


# ── health() when DB unreachable ─────────────────────────────────────────────


def test_health_unhealthy_when_no_db(device):
    with patch("devices.postgres.device._pg_connect", return_value=None):
        h = device.health()
    assert h["status"] == "unhealthy"
    assert h["connected"] is False
    assert "checked_at" in h


def test_health_healthy_with_mock_db(device):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (5,)
    with patch("devices.postgres.device._pg_connect", return_value=conn):
        h = device.health()
    assert h["status"] == "healthy"
    assert h["connected"] is True
    assert h["active_connections"] == 5


def test_health_degraded_on_query_error(device):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.side_effect = Exception("query failed")
    with patch("devices.postgres.device._pg_connect", return_value=conn):
        h = device.health()
    assert h["status"] == "degraded"
    assert "query failed" in h["detail"]


# ── _check_not_blocked ────────────────────────────────────────────────────────


def test_check_not_blocked_raises_when_registry_says_blocked(device_with_registry):
    device_with_registry._registry.get_device.return_value = {
        "status": "blocked",
        "block_type": "manual",
        "blocked_since": "2026-01-01T00:00:00",
    }
    with pytest.raises(DeviceBlockedError) as exc:
        device_with_registry._check_not_blocked()
    assert exc.value.info["device_id"] == "postgres"


def test_check_not_blocked_passes_when_status_is_online(device_with_registry):
    device_with_registry._registry.get_device.return_value = {"status": "online"}
    device_with_registry._check_not_blocked()  # must not raise


def test_check_not_blocked_passes_when_no_registry(device):
    device._registry = None
    device._check_not_blocked()  # must not raise


def test_health_raises_device_blocked_error_when_blocked(device_with_registry):
    device_with_registry._registry.get_device.return_value = {
        "status": "blocked",
        "block_type": "manual",
        "blocked_since": None,
    }
    with pytest.raises(DeviceBlockedError):
        device_with_registry.health()


# ── uptime() ─────────────────────────────────────────────────────────────────


def test_uptime_returns_zero_on_no_db(device):
    with patch("devices.postgres.device._pg_connect", return_value=None):
        assert device.uptime() == 0.0


def test_uptime_returns_positive_with_mock_db(device):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (1234.5,)
    with patch("devices.postgres.device._pg_connect", return_value=conn):
        assert device.uptime() == 1234.5


# ── Integration (real Postgres) ───────────────────────────────────────────────


@_skip_no_db
class TestPostgresIntegration:
    @pytest.fixture
    def dev(self):
        return PostgresDevice()

    def test_health_healthy(self, dev):
        h = dev.health()
        assert h["status"] == "healthy"
        assert h["connected"] is True
        assert isinstance(h["query_latency_ms"], float)

    def test_uptime_positive(self, dev):
        assert dev.uptime() > 0

    def test_logs_returns_log_dir(self, dev):
        logs = dev.logs()
        assert "paths" in logs

    def test_where_and_how_has_data_dir(self, dev):
        w = dev.where_and_how()
        assert "data_dir" in w

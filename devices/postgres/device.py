"""
PostgresDevice — first concrete BaseDevice implementation.

Serves as proof-of-concept that the BaseDevice contract works and the design
is self-consistent. Lifecycle (restart/block/halt) delegates to PostgresShim.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from agent_datacenter.device import BaseDevice, INTERFACE_VERSION
from agent_datacenter.skeleton.exceptions import DeviceBlockedError

if TYPE_CHECKING:
    from devices.postgres.shim import PostgresShim

log = logging.getLogger(__name__)

_START_TIME = time.time()


def _pg_version() -> str:
    try:
        import subprocess

        result = subprocess.run(
            ["psql", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip().split()[-1]
    except Exception:
        pass
    return "unknown"


def _pg_connect():
    """Return a psycopg2 connection or None."""
    try:
        import psycopg2

        url = (
            os.environ.get("AGENT_DATACENTER_DB_URL")
            or os.environ.get("AGENT_DATACENTER_POSTGRES_URL")
            or ""
        )
        if not url:
            return None
        return psycopg2.connect(url, connect_timeout=3)
    except Exception:
        return None


class PostgresDevice(BaseDevice):
    def __init__(self, shim: PostgresShim | None = None, registry=None) -> None:
        self._shim = shim
        self._registry = (
            registry  # DeviceRegistry; when set, ops check block status first
        )
        self._startup_errors: list[str] = []

    def _check_not_blocked(self) -> None:
        """Raise DeviceBlockedError immediately if this device is blocked in the registry.

        Prevents dependent devices from hanging on TCP timeout during a Postgres block
        (e.g. during a migration where Postgres is intentionally taken offline).
        """
        if self._registry is None:
            return
        record = self._registry.get_device("postgres")
        if record and record.get("status") == "blocked":
            raise DeviceBlockedError(
                device_id="postgres",
                block_type=record.get("block_type", "manual"),
                blocked_since=record.get("blocked_since"),
            )

    def who_am_i(self) -> dict:
        return {
            "device_id": "postgres",
            "name": "Postgres",
            "version": _pg_version(),
            "purpose": "primary relational store",
        }

    def requirements(self) -> dict:
        return {
            "port": int(os.environ.get("PGPORT", 5432)),
            "user": os.environ.get("PGUSER", ""),
            "db": os.environ.get("PGDATABASE", ""),
            "deps": ["psycopg2"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": [],
            "query": True,
            "write": True,
            "migrate": True,
        }

    def comms(self) -> dict:
        return {
            "address": "comms://postgres/inbox",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        self._check_not_blocked()
        conn = _pg_connect()
        if conn is None:
            return {
                "status": "unhealthy",
                "connected": False,
                "query_latency_ms": None,
                "active_connections": None,
                "detail": "connection failed",
                "checked_at": _now(),
            }
        try:
            t0 = time.monotonic()
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM pg_stat_activity")
            active = cur.fetchone()[0]
            latency_ms = (time.monotonic() - t0) * 1000
            return {
                "status": "healthy",
                "connected": True,
                "query_latency_ms": round(latency_ms, 2),
                "active_connections": active,
                "detail": "ok",
                "checked_at": _now(),
            }
        except Exception as e:
            return {
                "status": "degraded",
                "connected": True,
                "query_latency_ms": None,
                "active_connections": None,
                "detail": str(e),
                "checked_at": _now(),
            }
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def uptime(self) -> float:
        conn = _pg_connect()
        if conn is None:
            return 0.0
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM (NOW() - pg_postmaster_start_time()))"
            )
            return float(cur.fetchone()[0])
        except Exception:
            return 0.0
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def startup_errors(self) -> list:
        return list(self._startup_errors)

    def logs(self) -> dict:
        conn = _pg_connect()
        log_dir = ""
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("SHOW log_directory")
                log_dir = cur.fetchone()[0]
                conn.close()
            except Exception:
                pass
        return {
            "paths": {"main": log_dir},
            "format": "postgresql-%Y-%m-%d_%H%M%S.log",
        }

    def update_info(self) -> dict:
        return {
            "method": "pg_ctl or Docker",
            "current_version": _pg_version(),
            "update_available": False,
        }

    def where_and_how(self) -> dict:
        conn = _pg_connect()
        data_dir = socket_dir = hba = ""
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("SHOW data_directory")
                data_dir = cur.fetchone()[0]
                cur.execute("SHOW unix_socket_directories")
                socket_dir = cur.fetchone()[0]
                cur.execute("SHOW hba_file")
                hba = cur.fetchone()[0]
                conn.close()
            except Exception:
                pass
        return {
            "host": os.environ.get("PGHOST", "localhost"),
            "pid": _pg_pid(),
            "launch_command": "pg_ctl start",
            "data_dir": data_dir,
            "socket": socket_dir,
            "hba_file": hba,
        }

    def restart(self) -> None:
        if self._shim:
            self._shim.restart()

    def block(self, reason: str) -> None:
        log.warning("PostgresDevice blocked: %s", reason)
        if self._shim:
            self._shim.stop()

    def halt(self) -> None:
        if self._shim:
            self._shim.stop()

    def recovery(self) -> None:
        if self._shim:
            self._shim.start()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _pg_pid() -> int:
    try:
        import subprocess

        r = subprocess.run(
            ["pg_ctl", "status", "-D", os.environ.get("PGDATA", "")],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for token in r.stdout.split():
            if token.isdigit():
                return int(token)
    except Exception:
        pass
    return 0

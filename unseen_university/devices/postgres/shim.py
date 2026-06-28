"""
PostgresShim — Postgres lifecycle via pg_ctl (Docker fallback).

self_test does a write/read/rollback cycle against a dedicated schema
(_rack_self_test) to verify full DB access without touching application data.
The table is cleaned up after each test.

Docker fallback: if pg_ctl is not in PATH, falls back to
'docker exec <container> pg_ctl ...' where container name comes from
AGENT_DATACENTER_PG_CONTAINER env var (default: 'postgres').
"""

from __future__ import annotations

import logging
import os
import subprocess
import time

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)

_PG_DATA = os.environ.get("PGDATA", "")
_PG_CONTAINER = os.environ.get("AGENT_DATACENTER_PG_CONTAINER", "postgres")


def _pg_ctl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run pg_ctl; fall back to docker exec if pg_ctl not in PATH."""
    try:
        return subprocess.run(
            ["pg_ctl", *args, "-D", _PG_DATA],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        # Docker fallback
        log.info(
            "pg_ctl not found — using Docker fallback (container=%s)", _PG_CONTAINER
        )
        return subprocess.run(
            ["docker", "exec", _PG_CONTAINER, "pg_ctl", *args, "-D", _PG_DATA],
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def _pg_connect():
    try:
        import psycopg2

        url = (
            os.environ.get("AGENT_DATACENTER_DB_URL")
            or os.environ.get("AGENT_DATACENTER_POSTGRES_URL")
            or ""
        )
        if not url:
            return None
        return psycopg2.connect(url, connect_timeout=5)
    except Exception:
        return None


class PostgresShim(BaseShim):
    @property
    def device_id(self) -> str:
        return "postgres"

    def start(self) -> bool:
        result = _pg_ctl("start", "-l", "/tmp/pg_startup.log")
        if result.returncode != 0:
            log.error("pg_ctl start failed: %s", result.stderr)
            return False
        # Wait until connectable (up to 10s)
        for _ in range(20):
            conn = _pg_connect()
            if conn:
                conn.close()
                return True
            time.sleep(0.5)
        log.error("postgres started but not connectable after 10s")
        return False

    def stop(self) -> bool:
        result = _pg_ctl("stop", "-m", "fast")
        if result.returncode != 0:
            log.error("pg_ctl stop failed: %s", result.stderr)
            return False
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        """
        Write/read/rollback cycle against _rack_self_test schema.
        Verifies full DB access without touching application data.
        """
        conn = _pg_connect()
        if conn is None:
            return {"passed": False, "details": "connection failed"}
        try:
            t0 = time.monotonic()
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS _rack_self_test "
                "(id SERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
            cur.execute("INSERT INTO _rack_self_test (ts) VALUES (NOW()) RETURNING id")
            row_id = cur.fetchone()[0]
            cur.execute("SELECT id FROM _rack_self_test WHERE id = %s", (row_id,))
            assert cur.fetchone()[0] == row_id
            conn.rollback()  # leave no permanent state
            cur.execute("DROP TABLE IF EXISTS _rack_self_test")
            conn.commit()
            latency_ms = (time.monotonic() - t0) * 1000
            return {
                "passed": True,
                "details": f"write/read/rollback OK, latency: {latency_ms:.1f}ms",
            }
        except Exception as e:
            log.error("self_test failed: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass
            return {"passed": False, "details": str(e)}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def rollback(self) -> None:
        """Kill pg process and remove pid file if start() fails mid-way."""
        # Attempt fast stop; ignore errors (process may not be running)
        try:
            _pg_ctl("stop", "-m", "immediate", timeout=10)
        except Exception:
            pass
        pid_file = os.path.join(_PG_DATA, "postmaster.pid") if _PG_DATA else None
        if pid_file and os.path.exists(pid_file):
            try:
                os.remove(pid_file)
                log.info("removed stale postmaster.pid after failed start")
            except OSError as e:
                log.warning("could not remove postmaster.pid: %s", e)

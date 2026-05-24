"""Tests for shell_exec MCP tool — T-adc-shell-exec-tool."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def _db_reachable() -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(os.environ["IGOR_HOME_DB_URL"], connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


_db_available = _db_reachable()

pytestmark = pytest.mark.skipif(not _db_available, reason="Igor DB not reachable")


class TestShellExec:
    def test_echo_stdout(self):
        from unseen_university.devices.librarian.tools.exec_tools import shell_exec

        result = shell_exec("echo hello")
        assert result["stdout"].strip() == "hello"
        assert result["exit_code"] == 0
        assert result["timed_out"] is False

    def test_exit_nonzero(self):
        from unseen_university.devices.librarian.tools.exec_tools import shell_exec

        result = shell_exec("false")
        assert result["exit_code"] != 0
        assert result["timed_out"] is False

    def test_timeout(self):
        from unseen_university.devices.librarian.tools.exec_tools import shell_exec

        result = shell_exec("sleep 999", timeout_s=0.1)
        assert result["timed_out"] is True
        assert result["exit_code"] == -1

    def test_action_log_entry(self):
        import psycopg2
        import psycopg2.extras

        from unseen_university.devices.librarian.tools.exec_tools import shell_exec

        shell_exec("echo action_log_test_marker")

        conn = psycopg2.connect(os.environ["IGOR_HOME_DB_URL"])
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM adc.action_log WHERE tool_name = 'shell_exec' "
                    "AND device_id = 'librarian' ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row["exit_code"] == 0

    def test_max_timeout_capped(self):
        """timeout_s > 300 is silently capped at 300."""
        from unseen_university.devices.librarian.tools.exec_tools import shell_exec

        # Just verify it doesn't raise and returns a result
        result = shell_exec("echo capped", timeout_s=99999)
        assert result["exit_code"] == 0

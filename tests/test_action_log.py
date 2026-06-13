"""Tests for unseen_university.action_log — T-adc-action-log."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def _db_reachable() -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(os.environ["UU_HOME_DB_URL"], connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Igor DB not reachable")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _count_rows(tool_name: str, device_id: str = "test-device") -> int:
    import psycopg2

    conn = psycopg2.connect(os.environ["UU_HOME_DB_URL"])
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM adc.action_log "
                    "WHERE tool_name = %s AND device_id = %s",
                    (tool_name, device_id),
                )
                return cur.fetchone()[0]
            except psycopg2.errors.UndefinedTable:
                conn.rollback()
                return 0
    finally:
        conn.close()


def _last_row(tool_name: str, device_id: str = "test-device") -> dict:
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(os.environ["UU_HOME_DB_URL"])
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM adc.action_log "
                "WHERE tool_name = %s AND device_id = %s "
                "ORDER BY id DESC LIMIT 1",
                (tool_name, device_id),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestAppendAction:
    def test_inserts_row(self):
        from unseen_university.action_log import append_action

        before = _count_rows("test_tool_insert")
        append_action("test-device", "test_tool_insert", {"x": 1}, "ok")
        assert _count_rows("test_tool_insert") == before + 1

    def test_row_fields(self):
        from unseen_university.action_log import append_action

        append_action(
            "test-device",
            "test_tool_fields",
            {"cmd": "echo hi"},
            "stdout=hi",
            duration_ms=42,
            exit_code=0,
        )
        row = _last_row("test_tool_fields")
        assert row["device_id"] == "test-device"
        assert row["tool_name"] == "test_tool_fields"
        assert row["args_json"] == {"cmd": "echo hi"}
        assert row["result_summary"] == "stdout=hi"
        assert row["duration_ms"] == 42
        assert row["exit_code"] == 0
        assert row["ts"] is not None

    def test_none_args_stored_as_empty_dict(self):
        from unseen_university.action_log import append_action

        append_action("test-device", "test_tool_none_args", None, "ok")
        row = _last_row("test_tool_none_args")
        assert row["args_json"] == {}

    def test_no_raise_on_bad_url(self, monkeypatch):
        import unseen_university.action_log as al

        monkeypatch.setattr(al, "_PG_URL", "postgresql://nobody:x@localhost:1/bad")
        # Must not raise — fire-and-forget contract
        al.append_action("test-device", "test_tool_bad_url", {}, "ok")

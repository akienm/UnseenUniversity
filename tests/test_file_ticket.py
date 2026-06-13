"""Tests for file_ticket MCP tool — T-adc-file-ticket-tool."""

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


def _read_ticket(ticket_id: str) -> dict:
    import psycopg2

    conn = psycopg2.connect(os.environ["UU_HOME_DB_URL"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metadata FROM clan.memories WHERE id = %s AND parent_id = 'TICKETS_ROOT'",
                (ticket_id,),
            )
            row = cur.fetchone()
            # psycopg2 auto-deserializes JSONB to dict
            return row[0] if row else {}
    finally:
        conn.close()


def _last_action_log(tool_name: str) -> dict:
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(os.environ["UU_HOME_DB_URL"])
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM adc.action_log WHERE tool_name = %s AND device_id = 'librarian' "
                "ORDER BY id DESC LIMIT 1",
                (tool_name,),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


def _delete_ticket(ticket_id: str) -> None:
    """Remove a test-created ticket from clan.memories so it doesn't pollute the live queue."""
    import psycopg2

    conn = psycopg2.connect(os.environ["UU_HOME_DB_URL"])
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM clan.memories WHERE id = %s AND parent_id = 'TICKETS_ROOT'",
                    (ticket_id,),
                )
    finally:
        conn.close()


class TestFileTicket:
    def test_inserts_in_tickets_root(self):
        from unseen_university.devices.librarian.tools.ticket_tools import file_ticket

        result = file_ticket(
            title="test file ticket inserts row",
            description="automated test ticket",
        )
        ticket_id = result["ticket_id"]
        try:
            row = _read_ticket(ticket_id)
            assert row["kind"] == "ticket"
            assert row["id"] == ticket_id
            assert row["title"] == "test file ticket inserts row"
        finally:
            _delete_ticket(ticket_id)

    def test_metadata_fields(self):
        from unseen_university.devices.librarian.tools.ticket_tools import file_ticket

        result = file_ticket(
            title="test file ticket metadata",
            description="checks all fields",
            size="M",
            tags=["ADC", "Test"],
            decision_id="D-test-2026-01-01",
            priority=0.8,
            status="sprint",
        )
        ticket_id = result["ticket_id"]
        try:
            row = _read_ticket(ticket_id)
            assert row["description"] == "checks all fields"
            assert row["size"] == "M"
            assert row["tags"] == ["ADC", "Test"]
            assert row["decision_id"] == "D-test-2026-01-01"
            assert row["priority"] == 0.8
            assert row["status"] == "sprint"
        finally:
            _delete_ticket(ticket_id)

    def test_action_log_entry(self):
        from unseen_university.devices.librarian.tools.ticket_tools import file_ticket

        result = file_ticket(
            title="test file ticket action log",
            description="verify action log entry",
        )
        try:
            log_row = _last_action_log("file_ticket")
            assert log_row
            assert log_row["device_id"] == "librarian"
            assert "T-test-file-ticket-action-log" in str(log_row["args_json"])
        finally:
            _delete_ticket(result["ticket_id"])

    def test_upsert_on_conflict(self):
        """Second call with same title updates rather than errors."""
        from unseen_university.devices.librarian.tools.ticket_tools import file_ticket

        file_ticket(title="test upsert ticket", description="first")
        result = file_ticket(title="test upsert ticket", description="second")
        ticket_id = result["ticket_id"]
        try:
            row = _read_ticket(ticket_id)
            assert row["description"] == "second"
        finally:
            _delete_ticket(ticket_id)

"""Tests for file_read / file_write MCP tools — T-adc-file-rw-tools."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
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


class TestFileRW:
    def test_write_and_read_roundtrip(self):
        from unseen_university.devices.librarian.tools.file_tools import (
            file_read,
            file_write,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            tmp = f.name

        try:
            file_write(tmp, "hello test content")
            result = file_read(tmp)
            assert result["content"] == "hello test content"
            assert result["path"] == tmp
            assert result["size_bytes"] > 0
            assert result["encoding"] == "utf-8"
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_write_action_log(self):
        from unseen_university.devices.librarian.tools.file_tools import file_write

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            tmp = f.name

        try:
            file_write(tmp, "logged content")
            row = _last_action_log("file_write")
            assert row
            assert row["device_id"] == "librarian"
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_read_action_log(self):
        from unseen_university.devices.librarian.tools.file_tools import (
            file_read,
            file_write,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            tmp = f.name

        try:
            file_write(tmp, "read test")
            file_read(tmp)
            row = _last_action_log("file_read")
            assert row
            assert row["device_id"] == "librarian"
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_write_mkdir_creates_parents(self):
        from unseen_university.devices.librarian.tools.file_tools import file_write

        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "a" / "b" / "c" / "test.txt")
            result = file_write(path, "nested", mkdir=True)
            assert Path(path).exists()
            assert result["written_bytes"] > 0

    def test_append_mode(self):
        from unseen_university.devices.librarian.tools.file_tools import (
            file_read,
            file_write,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            tmp = f.name

        try:
            file_write(tmp, "line1\n")
            file_write(tmp, "line2\n", mode="a")
            result = file_read(tmp)
            assert result["content"] == "line1\nline2\n"
        finally:
            Path(tmp).unlink(missing_ok=True)

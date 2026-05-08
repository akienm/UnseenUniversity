"""Tests for librarian palace_tools — ls, read, write, search.

Integration tests against a real Postgres DB (adc.palace in a test schema).
Requires IGOR_HOME_DB_URL.
"""

from __future__ import annotations

import json
import os
import random

import psycopg2
import pytest

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_PREFIX = f"palace.test_{random.randint(10_000_000, 99_999_999)}"


@pytest.fixture(scope="module", autouse=True)
def seed_nodes():
    """Seed a small subtree; tear it down after the module."""
    from agent_datacenter.devices.librarian.tools.palace_tools import palace_write

    palace_write(
        f"{_PREFIX}.alpha",
        "Alpha Node",
        "content about alpha extraction",
        tags=["test", "alpha"],
    )
    palace_write(
        f"{_PREFIX}.beta", "Beta Node", "content about beta testing", tags=["test"]
    )
    palace_write(
        f"{_PREFIX}.alpha.child",
        "Alpha Child",
        "child node content",
        node_type="doc",
        tags=["test"],
    )
    yield
    with psycopg2.connect(_PG_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adc.palace WHERE path LIKE %s", (f"{_PREFIX}%",))


# ── palace_ls ─────────────────────────────────────────────────────────────────


class TestPalaceLs:
    def test_lists_nodes_under_prefix(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_ls

        result = palace_ls(_PREFIX)
        assert "Alpha Node" in result
        assert "Beta Node" in result

    def test_lists_child_nodes(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_ls

        result = palace_ls(f"{_PREFIX}.alpha")
        assert "alpha" in result
        assert "child" in result

    def test_empty_prefix_not_empty(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_ls

        result = palace_ls("")
        assert "node(s)" in result

    def test_unknown_prefix_returns_message(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_ls

        result = palace_ls("palace.nonexistent_xyz_999")
        assert "No nodes found" in result

    def test_limit_respected(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_ls

        result = palace_ls(_PREFIX, limit=1)
        # Only 1 result line after the header
        content_lines = [l for l in result.splitlines() if "—" in l]
        assert len(content_lines) == 1


# ── palace_read ───────────────────────────────────────────────────────────────


class TestPalaceRead:
    def test_reads_known_node(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_read

        result = palace_read(f"{_PREFIX}.alpha")
        assert "Alpha Node" in result
        assert "content about alpha" in result

    def test_unknown_path_returns_message(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_read

        result = palace_read("palace.does.not.exist.xyz")
        assert "No node found" in result

    def test_result_includes_node_type(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_read

        result = palace_read(f"{_PREFIX}.alpha")
        assert "node_type" in result
        assert "doc" in result

    def test_result_includes_tags(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_read

        result = palace_read(f"{_PREFIX}.alpha")
        assert "test" in result
        assert "alpha" in result


# ── palace_write ──────────────────────────────────────────────────────────────


class TestPalaceWrite:
    def test_write_creates_node(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import (
            palace_read,
            palace_write,
        )

        path = f"{_PREFIX}.write_test"
        palace_write(path, "Write Test", "written content", tags=["test"])
        result = palace_read(path)
        assert "Write Test" in result
        assert "written content" in result

    def test_write_is_idempotent(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_write

        path = f"{_PREFIX}.idem_test"
        palace_write(path, "Idem", "v1")
        result = palace_write(path, "Idem Updated", "v2")
        assert "Written" in result

    def test_write_returns_path_and_timestamp(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_write

        result = palace_write(f"{_PREFIX}.ts_test", "TS", "content")
        assert f"{_PREFIX}.ts_test" in result
        assert "updated_at" in result


# ── palace_search ─────────────────────────────────────────────────────────────


class TestPalaceSearch:
    def test_finds_node_by_content(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_search

        result = palace_search("alpha extraction")
        assert _PREFIX in result

    def test_tag_filter_narrows_results(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_search

        # alpha tag should match alpha node but not beta
        result = palace_search("content", tags=["alpha"])
        assert f"{_PREFIX}.alpha" in result
        assert f"{_PREFIX}.beta" not in result

    def test_no_match_returns_message(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_search

        result = palace_search("xyzzy_nonexistent_term_9q8w7e")
        assert "No results" in result

    def test_limit_respected(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_search

        result = palace_search("content", limit=1)
        # Should have at most 1 result block (heuristic: count path occurrences)
        assert result.count(f"{_PREFIX}") <= 1


# ── dispatch wiring ───────────────────────────────────────────────────────────


class TestDispatch:
    def test_dispatch_palace_ls(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import dispatch

        result = dispatch("palace_ls", {"prefix": _PREFIX})
        assert result is not None
        assert "Alpha" in result

    def test_dispatch_palace_read(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import dispatch

        result = dispatch("palace_read", {"path": f"{_PREFIX}.beta"})
        assert "Beta Node" in result

    def test_dispatch_palace_write(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import dispatch

        result = dispatch(
            "palace_write",
            {"path": f"{_PREFIX}.dispatch_test", "title": "D", "content": "c"},
        )
        assert "Written" in result

    def test_dispatch_palace_search(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import dispatch

        result = dispatch("palace_search", {"query": "beta testing"})
        assert result is not None

    def test_dispatch_unknown_returns_none(self):
        from agent_datacenter.devices.librarian.tools.palace_tools import dispatch

        assert dispatch("not_a_palace_tool", {}) is None

    def test_schemas_registered_in_init(self):
        from agent_datacenter.devices.librarian.tools import SCHEMAS

        names = {s["name"] for s in SCHEMAS}
        assert {"palace_ls", "palace_read", "palace_write", "palace_search"} <= names

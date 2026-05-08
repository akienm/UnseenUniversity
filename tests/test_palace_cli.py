"""Tests for scripts/palace_cli.py — ls, read, search, edit, delete.

Integration tests against a real Postgres DB (adc.palace in a test schema).
Requires IGOR_HOME_DB_URL.
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import psycopg2
import pytest

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_PREFIX = f"palace.test_cli_{random.randint(10_000_000, 99_999_999)}"


@pytest.fixture(scope="module", autouse=True)
def seed_nodes():
    from agent_datacenter.devices.librarian.tools.palace_tools import palace_write

    palace_write(
        f"{_PREFIX}.alpha", "Alpha CLI", "alpha cli content", tags=["test", "alpha"]
    )
    palace_write(f"{_PREFIX}.beta", "Beta CLI", "beta cli content", tags=["test"])
    yield
    with psycopg2.connect(_PG_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adc.palace WHERE path LIKE %s", (f"{_PREFIX}%",))


def run_cli(
    argv: list[str], capsys, input_text: str | None = None
) -> tuple[str, str, int]:
    """Run palace_cli.py main() with given argv. Returns (stdout, stderr, exit_code)."""
    import palace_cli

    old_argv = sys.argv
    sys.argv = ["palace_cli.py"] + argv
    exit_code = 0
    try:
        if input_text is not None:
            import io

            old_stdin = sys.stdin
            sys.stdin = io.StringIO(input_text)
            try:
                palace_cli.main()
            finally:
                sys.stdin = old_stdin
        else:
            palace_cli.main()
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old_argv
    captured = capsys.readouterr()
    return captured.out, captured.err, exit_code


# ── ls ────────────────────────────────────────────────────────────────────────


class TestLs:
    def test_lists_seeded_nodes(self, capsys):
        out, _, code = run_cli(["ls", _PREFIX], capsys)
        assert code == 0
        assert "Alpha CLI" in out
        assert "Beta CLI" in out

    def test_limit_respected(self, capsys):
        out, _, code = run_cli(["ls", _PREFIX, "--limit", "1"], capsys)
        assert code == 0
        content_lines = [l for l in out.splitlines() if "—" in l]
        assert len(content_lines) == 1

    def test_json_flag(self, capsys):
        out, _, code = run_cli(["ls", _PREFIX, "--json"], capsys)
        assert code == 0
        data = json.loads(out)
        assert "result" in data

    def test_unknown_prefix(self, capsys):
        out, _, code = run_cli(["ls", "palace.nope_xyz_999"], capsys)
        assert code == 0
        assert "No nodes found" in out


# ── read ──────────────────────────────────────────────────────────────────────


class TestRead:
    def test_reads_known_node(self, capsys):
        out, _, code = run_cli(["read", f"{_PREFIX}.alpha"], capsys)
        assert code == 0
        assert "Alpha CLI" in out
        assert "alpha cli content" in out

    def test_unknown_path(self, capsys):
        out, _, code = run_cli(["read", "palace.does.not.exist.xyz"], capsys)
        assert code == 0
        assert "No node found" in out

    def test_json_flag(self, capsys):
        out, _, code = run_cli(["read", f"{_PREFIX}.alpha", "--json"], capsys)
        assert code == 0
        data = json.loads(out)
        assert "Alpha CLI" in data["result"]


# ── search ────────────────────────────────────────────────────────────────────


class TestSearch:
    def test_finds_seeded_content(self, capsys):
        out, _, code = run_cli(["search", "alpha cli content"], capsys)
        assert code == 0
        assert _PREFIX in out

    def test_tag_filter(self, capsys):
        out, _, code = run_cli(["search", "content", "--tag", "alpha"], capsys)
        assert code == 0
        assert f"{_PREFIX}.alpha" in out
        assert f"{_PREFIX}.beta" not in out

    def test_no_match(self, capsys):
        out, _, code = run_cli(["search", "xyzzy_totally_absent_9q8w7e"], capsys)
        assert code == 0
        assert "No results" in out

    def test_json_flag(self, capsys):
        out, _, code = run_cli(["search", "alpha cli content", "--json"], capsys)
        assert code == 0
        data = json.loads(out)
        assert "result" in data


# ── edit ──────────────────────────────────────────────────────────────────────


class TestEdit:
    def test_creates_new_node(self, capsys):
        path = f"{_PREFIX}.cli_new"
        out, _, code = run_cli(
            ["edit", path, "--title", "CLI New", "--content", "new content"], capsys
        )
        assert code == 0
        assert "Written" in out

    def test_updates_existing_node(self, capsys):
        path = f"{_PREFIX}.alpha"
        out, _, code = run_cli(["edit", path, "--title", "Alpha CLI Updated"], capsys)
        assert code == 0
        assert "Written" in out
        # Verify update took
        out2, _, _ = run_cli(["read", path], capsys)
        assert "Alpha CLI Updated" in out2

    def test_create_without_title_fails(self, capsys):
        path = f"{_PREFIX}.missing_title_xyz"
        _, err, code = run_cli(["edit", path, "--content", "some content"], capsys)
        assert code != 0

    def test_json_flag(self, capsys):
        path = f"{_PREFIX}.cli_json_edit"
        out, _, code = run_cli(
            ["edit", path, "--title", "J", "--content", "c", "--json"], capsys
        )
        assert code == 0
        data = json.loads(out)
        assert "Written" in data["result"]


# ── delete ────────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_with_yes_flag(self, capsys):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_write

        path = f"{_PREFIX}.to_delete"
        palace_write(path, "To Delete", "delete me")
        out, _, code = run_cli(["delete", path, "--yes"], capsys)
        assert code == 0
        assert "Deleted" in out

    def test_delete_confirms_interactive(self, capsys):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_write

        path = f"{_PREFIX}.to_delete_interactive"
        palace_write(path, "To Delete Interactive", "delete me too")
        out, _, code = run_cli(["delete", path], capsys, input_text="y\n")
        assert code == 0
        assert "Deleted" in out

    def test_delete_abort(self, capsys):
        path = f"{_PREFIX}.alpha"
        out, _, code = run_cli(["delete", path], capsys, input_text="n\n")
        assert code != 0
        assert "Aborted" in out

    def test_delete_nonexistent(self, capsys):
        out, _, code = run_cli(["delete", "palace.nope_xyz_404", "--yes"], capsys)
        assert code != 0
        assert "No node found" in out

    def test_delete_json_flag(self, capsys):
        from agent_datacenter.devices.librarian.tools.palace_tools import palace_write

        path = f"{_PREFIX}.to_delete_json"
        palace_write(path, "To Delete JSON", "json delete")
        out, _, code = run_cli(["delete", path, "--yes", "--json"], capsys)
        assert code == 0
        data = json.loads(out)
        assert "Deleted" in data["result"]

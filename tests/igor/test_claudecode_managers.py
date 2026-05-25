"""
tests/test_claudecode_managers.py — Unit tests for session_manager.py + decision_manager.py.

Tests cover pure/file functions only — no Postgres required.
DB-dependent functions use mocks.

Ref: T-test-debt-tooling
"""

from __future__ import annotations

import re
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _add_repo_to_path():
    repo = Path(__file__).parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    cc = repo / "lab" / "claudecode"
    if str(cc) not in sys.path:
        sys.path.insert(0, str(cc))


_add_repo_to_path()

import decision_manager as dm
import session_manager as sm

# ═══════════════════════════════════════════════════════════════════════════════
# session_manager — pure function tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseSessionsMd(unittest.TestCase):
    """_parse_sessions_md: sessions.md → list of dicts, no DB."""

    def _md(self, text: str) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        )
        tmp.write(textwrap.dedent(text))
        tmp.flush()
        return Path(tmp.name)

    def test_parses_single_session(self):
        md = self._md("""
            ## Session 2026-03-19a
            **Theme**: Test session
            **Decisions**: D001, D002
            **Key changes**:
            - Did thing A
            - Did thing B
            **Next session**: Do thing C
            **In-flight**: NONE
        """)
        sessions = sm._parse_sessions_md(md)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["id"], "2026-03-19a")
        self.assertEqual(s["theme"], "Test session")
        self.assertIn("D001", s["decisions"])
        self.assertIn("Did thing A", s["key_changes"])
        self.assertEqual(s["next_session"], "Do thing C")
        self.assertEqual(s["in_flight"], "NONE")

    def test_parses_multiple_sessions_preserves_order(self):
        md = self._md("""
            ## Session 2026-03-19b
            **Theme**: Second session
            **Decisions**: D003
            **Key changes**:
            - Did thing C
            **Next session**: Do thing D
            **In-flight**: NONE

            ## Session 2026-03-19a
            **Theme**: First session
            **Decisions**: D001
            **Key changes**:
            - Did thing A
            **Next session**: Do thing B
            **In-flight**: NONE
        """)
        sessions = sm._parse_sessions_md(md)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["id"], "2026-03-19b")
        self.assertEqual(sessions[1]["id"], "2026-03-19a")

    def test_skips_non_session_blocks(self):
        md = self._md("""
            # TheIgors Sessions

            Some preamble text.

            ## Session 2026-03-01a
            **Theme**: Only session
            **Decisions**: D010
            **Key changes**:
            - Something
            **Next session**: Next
            **In-flight**: NONE
        """)
        sessions = sm._parse_sessions_md(md)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], "2026-03-01a")

    def test_missing_optional_fields_default_empty(self):
        md = self._md("""
            ## Session 2026-03-01b
            **Theme**: Minimal session
        """)
        sessions = sm._parse_sessions_md(md)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["decisions"], "")
        self.assertEqual(s["key_changes"], "")


class TestCurrentSessionId(unittest.TestCase):
    """current_session_id() reads from state file; returns '' on missing."""

    def test_returns_empty_when_file_missing(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "no_such_file.txt"
            with patch.object(sm, "CURRENT_SESSION_FILE", missing):
                result = sm.current_session_id()
        self.assertEqual(result, "")

    def test_returns_stripped_content(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("2026-03-19c\n")
            fpath = Path(f.name)
        with patch.object(sm, "CURRENT_SESSION_FILE", fpath):
            result = sm.current_session_id()
        self.assertEqual(result, "2026-03-19c")


# ═══════════════════════════════════════════════════════════════════════════════
# decision_manager — pure/file function tests
# ═══════════════════════════════════════════════════════════════════════════════

# Real DSB format: no `latest=` in header (updated= only)
_MINIMAL_DSB = textwrap.dedent("""\
    DOC|decisions_log|v1|updated=2026-01-01
    META|purpose=test
    D001|first-decision|implemented|The first decision
""")


def _write_dsb(text: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".dsb", delete=False, encoding="utf-8"
    )
    tmp.write(text)
    tmp.flush()
    return Path(tmp.name)


def _decision_lines(content: str) -> list[str]:
    """Return only Dxxx| lines (excludes DOC|, META| etc.)."""
    return [l for l in content.splitlines() if re.match(r"^D\d+\|", l)]


class TestUpdateDsb(unittest.TestCase):
    """_update_dsb: appends decision line to DSB, updates header."""

    def setUp(self):
        self._orig_dsb = dm.DSB_FILE

    def tearDown(self):
        dm.DSB_FILE = self._orig_dsb

    def test_appends_new_decision_after_existing(self):
        dsb = _write_dsb(_MINIMAL_DSB)
        dm.DSB_FILE = dsb
        dm._update_dsb("D002", "second-decision", "planned", "The second decision")
        lines = _decision_lines(dsb.read_text())
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "D001|first-decision|implemented|The first decision")
        self.assertEqual(lines[1], "D002|second-decision|planned|The second decision")

    def test_updates_header_date(self):
        from datetime import datetime

        dsb = _write_dsb(_MINIMAL_DSB)
        dm.DSB_FILE = dsb
        dm._update_dsb("D999", "new", "implemented", "desc")
        header = dsb.read_text().splitlines()[0]
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertIn(f"updated={today}", header)

    def test_returns_pipe_delimited_line(self):
        dsb = _write_dsb(_MINIMAL_DSB)
        dm.DSB_FILE = dsb
        result = dm._update_dsb("D050", "my-decision", "defined", "Some description")
        self.assertEqual(result, "D050|my-decision|defined|Some description")

    def test_empty_dsb_still_inserts(self):
        dsb = _write_dsb("DOC|decisions_log|v1|updated=2026-01-01\n")
        dm.DSB_FILE = dsb
        dm._update_dsb("D001", "first", "implemented", "First ever decision")
        lines = _decision_lines(dsb.read_text())
        self.assertEqual(len(lines), 1)
        self.assertIn("D001|first|implemented|First ever decision", lines[0])

    def test_multiple_decisions_accumulate_in_order(self):
        dsb = _write_dsb(_MINIMAL_DSB)
        dm.DSB_FILE = dsb
        dm._update_dsb("D002", "second", "planned", "Second")
        dm._update_dsb("D003", "third", "defined", "Third")
        ids = [l.split("|")[0] for l in _decision_lines(dsb.read_text())]
        self.assertEqual(ids, ["D001", "D002", "D003"])


class TestDecisionManagerCmdAdd(unittest.TestCase):
    """cmd_add: orchestrates DSB update + DB upsert + Igor flush."""

    def setUp(self):
        self._orig_dsb = dm.DSB_FILE

    def tearDown(self):
        dm.DSB_FILE = self._orig_dsb

    def test_cmd_add_calls_update_dsb(self):
        dsb = _write_dsb(_MINIMAL_DSB)
        dm.DSB_FILE = dsb
        with patch.object(dm, "_upsert_docs_entry"), patch.object(dm, "_flush_to_igor"):
            dm.cmd_add(["D042", "x", "implemented", "desc"])
        self.assertIn("D042", _decision_lines(dsb.read_text())[1])

    def test_cmd_add_uppercases_decision_id(self):
        dsb = _write_dsb(_MINIMAL_DSB)
        dm.DSB_FILE = dsb
        with patch.object(dm, "_upsert_docs_entry"), patch.object(dm, "_flush_to_igor"):
            dm.cmd_add(["d042", "x", "implemented", "desc"])
        ids = [l.split("|")[0] for l in _decision_lines(dsb.read_text())]
        self.assertIn("D042", ids)

    def test_cmd_add_exits_on_too_few_args(self):
        with self.assertRaises(SystemExit):
            dm.cmd_add(["D042", "only-two"])


class TestSessionManagerCmdStart(unittest.TestCase):
    """cmd_start: creates partial session record + writes state file."""

    def test_cmd_start_writes_current_session_file(self):
        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_conn.__enter__ = MagicMock(return_value=fake_conn)
        fake_conn.__exit__ = MagicMock(return_value=False)
        fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cur)
        fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(sm, "_conn", return_value=fake_conn), patch.object(
            sm, "_ensure_table"
        ), patch.object(sm, "_write_current_session") as mock_write:
            sm.cmd_start(["2026-03-20x", "Test theme"])

        mock_write.assert_called_once_with("2026-03-20x")

    def test_cmd_start_exits_on_too_few_args(self):
        with self.assertRaises(SystemExit):
            sm.cmd_start(["only-one-arg"])


if __name__ == "__main__":
    unittest.main()

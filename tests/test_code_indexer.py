"""
Tests for devlab/claudecode/code_indexer.py — multi-language code index sweep.

Tests:
- read_existing_intent: extracts # intent: line or returns ''
- inject_intent_comment: idempotent; respects shebang; replaces existing
- file_hash: consistent MD5
- sweep_shell_files dry_run: counts files without writing
- run_sweep dry_run: Python + shell combined counters
- multi-language dispatch: both python and shell paths exercised
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devlab.claudecode.code_indexer import (
    _INTENT_SYMBOL,
    file_hash,
    inject_intent_comment,
    read_existing_intent,
    run_sweep,
    sweep_shell_files,
)


# ── read_existing_intent ──────────────────────────────────────────────────────

def test_read_existing_intent_found(tmp_path):
    f = tmp_path / "a.sh"
    f.write_text("#!/usr/bin/env bash\n# intent: manages the foo lifecycle\necho hi\n")
    assert read_existing_intent(f) == "manages the foo lifecycle"


def test_read_existing_intent_missing(tmp_path):
    f = tmp_path / "a.sh"
    f.write_text("#!/usr/bin/env bash\necho hi\n")
    assert read_existing_intent(f) == ""


def test_read_existing_intent_nonexistent(tmp_path):
    assert read_existing_intent(tmp_path / "ghost.sh") == ""


# ── inject_intent_comment ─────────────────────────────────────────────────────

def test_inject_after_shebang(tmp_path):
    f = tmp_path / "a.sh"
    f.write_text("#!/usr/bin/env bash\necho hi\n")
    inject_intent_comment(f, "does the thing")
    lines = f.read_text().splitlines()
    assert lines[0].startswith("#!")
    assert lines[1] == "# intent: does the thing"


def test_inject_no_shebang(tmp_path):
    f = tmp_path / "a.sh"
    f.write_text("echo hi\n")
    inject_intent_comment(f, "does the thing")
    lines = f.read_text().splitlines()
    assert lines[0] == "# intent: does the thing"


def test_inject_replaces_existing(tmp_path):
    f = tmp_path / "a.sh"
    f.write_text("#!/usr/bin/env bash\n# intent: old intent\necho hi\n")
    inject_intent_comment(f, "new intent")
    content = f.read_text()
    assert "# intent: new intent" in content
    assert "# intent: old intent" not in content
    assert content.count("# intent:") == 1


def test_inject_idempotent(tmp_path):
    f = tmp_path / "a.sh"
    f.write_text("#!/usr/bin/env bash\necho hi\n")
    inject_intent_comment(f, "same intent")
    inject_intent_comment(f, "same intent")
    assert f.read_text().count("# intent:") == 1


# ── file_hash ─────────────────────────────────────────────────────────────────

def test_file_hash_consistent(tmp_path):
    f = tmp_path / "a.sh"
    f.write_text("content")
    h1 = file_hash(f)
    h2 = file_hash(f)
    assert h1 == h2
    assert len(h1) == 32  # MD5 hex


def test_file_hash_changes_with_content(tmp_path):
    f = tmp_path / "a.sh"
    f.write_text("content A")
    h1 = file_hash(f)
    f.write_text("content B")
    h2 = file_hash(f)
    assert h1 != h2


# ── sweep_shell_files (dry_run) ───────────────────────────────────────────────

def test_sweep_shell_files_dry_run(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.sh").write_text("#!/usr/bin/env bash\necho a\n")
    (repo / "b.sh").write_text("#!/usr/bin/env bash\necho b\n")
    (repo / "skip.py").write_text("print('skip')")

    result = sweep_shell_files(repo, db_url="unused", dry_run=True)
    assert result["inserted"] == 2
    assert result["errors"] == 0


def test_sweep_shell_files_dry_run_no_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = sweep_shell_files(repo, db_url="unused", dry_run=True)
    assert result == {"inserted": 0, "updated": 0, "unchanged": 0, "errors": 0}


def test_sweep_shell_files_targeted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    a = repo / "a.sh"
    b = repo / "b.sh"
    a.write_text("#!/usr/bin/env bash\necho a\n")
    b.write_text("#!/usr/bin/env bash\necho b\n")

    result = sweep_shell_files(repo, db_url="unused", dry_run=True, files=[a])
    assert result["inserted"] == 1


# ── run_sweep (dry_run, combined) ─────────────────────────────────────────────

def test_run_sweep_dry_run_counts_both(tmp_path):
    repo = tmp_path / "repo"
    (repo / "devices" / "pkg").mkdir(parents=True)
    (repo / "devices" / "pkg" / "foo.py").write_text("def bar(): pass")
    (repo / "config.sh").write_text("#!/usr/bin/env bash\necho hi\n")

    result = run_sweep(repo_root=repo, db_url="unused", dry_run=True)
    # Python: 1 symbol (bar)
    # Shell: 1 file
    assert result["inserted"] >= 2
    assert result["errors"] == 0


# ── DB write (mocked psycopg2) ────────────────────────────────────────────────

def test_sweep_inserts_new_shell_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sh = repo / "test.sh"
    sh.write_text("#!/usr/bin/env bash\necho test\n")

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = None  # no existing row

    with patch("devlab.claudecode.code_indexer._haiku_intent", return_value="test script echoes test"), \
         patch("psycopg2.connect", return_value=mock_conn):
        result = sweep_shell_files(repo, db_url="postgresql://test/db")

    assert result["inserted"] == 1
    assert result["errors"] == 0
    # File should now have # intent: comment
    assert "# intent: test script echoes test" in sh.read_text()


def test_sweep_skips_unchanged_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sh = repo / "test.sh"
    sh.write_text("#!/usr/bin/env bash\n# intent: existing intent\necho test\n")
    existing_hash = file_hash(sh)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = (existing_hash,)  # same hash

    with patch("psycopg2.connect", return_value=mock_conn):
        result = sweep_shell_files(repo, db_url="postgresql://test/db")

    assert result["unchanged"] == 1
    assert result["inserted"] == 0

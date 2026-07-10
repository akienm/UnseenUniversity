"""
Tests for devlab/claudecode/code_indexer.py — multi-language code index sweep.

Tests:
- read_existing_intent: extracts a legacy # intent: line or returns ''
- file_hash: consistent MD5
- sweep_shell_files dry_run: counts files without writing
- sweep_shell_files: indexing a source file does NOT mutate its bytes
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

def test_sweep_does_not_mutate_source(tmp_path):
    """Proof node (T-code-indexer-mutates-source): indexing a shell file records
    the intent in the DB row but leaves the source file's bytes untouched. Red form:
    restore the old inject_intent_comment call and the file is rewritten -> this fails."""
    repo = tmp_path / "repo"
    repo.mkdir()
    sh = repo / "test.sh"
    original = "#!/usr/bin/env bash\necho test\n"
    sh.write_text(original)
    bytes_before = sh.read_bytes()

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

    # The DB write still happens (intent recorded in clan.code_index) ...
    assert result["inserted"] == 1
    assert result["errors"] == 0
    # ... but the source file is byte-for-byte unchanged — no in-band mutation.
    assert sh.read_bytes() == bytes_before
    assert "# intent:" not in sh.read_text()


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

"""
Tests for devlab/claudecode/cc_nightly_palace_updates.py.

Tests:
- _parse_decision_doc: extracts title, date, status, spawned_tickets, narrative, hypothesis
- _parse_decision_doc: returns None when required fields are missing
- _parse_decision_doc: filters non-T-prefix entries from spawned_tickets
- scan_decision_docs: returns only docs matching date_filter
- scan_decision_docs: returns all docs when all_docs=True
- scan_decision_docs: returns empty list when directory does not exist
- write_decision_nodes: dry_run prints and returns count without DB write
- write_decision_nodes: upserts each doc via DB connection (mocked)
- write_session_brief: dry_run returns True without DB write
- write_session_brief: reads Done today section from slate
- run: end-to-end dry_run returns expected summary keys
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devlab.claudecode.cc_nightly_palace_updates import (
    _parse_decision_doc,
    _read_slate_done,
    run,
    scan_decision_docs,
    write_decision_nodes,
    write_session_brief,
)


# ── helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _redirect_memory_root(tmp_path, monkeypatch):
    """Point the slate_store resolver at the test's tmp_path (UU_MEMORY_ROOT)."""
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))


def _write_decision(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


_SAMPLE_DOC = """\
# D-sample-decision-2026-06-13
**title:** Sample decision for testing
**date:** 2026-06-13
**status:** open
**spawned_tickets:** T-sample-ticket, T-consequence-sample

## Decision narrative
This is the decision narrative. It explains what was decided and why.

## Hypothesis
Things will be better after this.

## Measurement Signal
Check the log for SUCCESS lines.

## Goal Link
none: testing only
"""


_MINIMAL_DOC = """\
**title:** Minimal
**date:** 2026-06-10
**status:** closed
"""


_INVALID_DOC = """\
# D-no-title
**status:** open
"""


# ── _parse_decision_doc ───────────────────────────────────────────────────────

def test_parse_extracts_required_fields(tmp_path):
    path = _write_decision(tmp_path, "D-sample-2026-06-13.md", _SAMPLE_DOC)
    doc = _parse_decision_doc(path)
    assert doc is not None
    assert doc["title"] == "Sample decision for testing"
    assert doc["date"] == "2026-06-13"
    assert doc["status"] == "open"


def test_parse_extracts_spawned_tickets(tmp_path):
    path = _write_decision(tmp_path, "D-sample-2026-06-13.md", _SAMPLE_DOC)
    doc = _parse_decision_doc(path)
    assert doc is not None
    assert "T-sample-ticket" in doc["spawned_tickets"]
    assert "T-consequence-sample" in doc["spawned_tickets"]


def test_parse_filters_non_ticket_spawned(tmp_path):
    content = _SAMPLE_DOC.replace(
        "**spawned_tickets:** T-sample-ticket, T-consequence-sample",
        "**spawned_tickets:** T-valid, IGOR-123, NotATicket",
    )
    path = _write_decision(tmp_path, "D-sample-2026-06-13.md", content)
    doc = _parse_decision_doc(path)
    assert doc is not None
    assert doc["spawned_tickets"] == ["T-valid"]


def test_parse_extracts_narrative_and_hypothesis(tmp_path):
    path = _write_decision(tmp_path, "D-sample-2026-06-13.md", _SAMPLE_DOC)
    doc = _parse_decision_doc(path)
    assert doc is not None
    assert "narrative" in doc["narrative"]
    assert "Things will be better" in doc["hypothesis"]


def test_parse_returns_none_when_title_missing(tmp_path):
    path = _write_decision(tmp_path, "D-invalid.md", _INVALID_DOC)
    doc = _parse_decision_doc(path)
    assert doc is None


def test_parse_returns_none_when_date_missing(tmp_path):
    content = "**title:** No date here\n**status:** open\n"
    path = _write_decision(tmp_path, "D-nodate.md", content)
    doc = _parse_decision_doc(path)
    assert doc is None


def test_parse_strips_parenthetical_notes_from_tickets(tmp_path):
    content = _SAMPLE_DOC.replace(
        "**spawned_tickets:** T-sample-ticket, T-consequence-sample",
        "**spawned_tickets:** T-core (updated), T-consequence (new)",
    )
    path = _write_decision(tmp_path, "D-sample.md", content)
    doc = _parse_decision_doc(path)
    assert doc is not None
    assert "T-core" in doc["spawned_tickets"]
    assert "(updated)" not in " ".join(doc["spawned_tickets"])


# ── scan_decision_docs ────────────────────────────────────────────────────────

def test_scan_filters_by_date(tmp_path):
    _write_decision(tmp_path, "D-today-2026-06-13.md", _SAMPLE_DOC)
    _write_decision(tmp_path, "D-old-2026-06-10.md", _MINIMAL_DOC)

    with patch("devlab.claudecode.cc_nightly_palace_updates._DECISIONS_DIR", tmp_path):
        docs = scan_decision_docs(date_filter="2026-06-13")

    assert len(docs) == 1
    assert docs[0]["date"] == "2026-06-13"


def test_scan_returns_all_when_all_docs_true(tmp_path):
    _write_decision(tmp_path, "D-today-2026-06-13.md", _SAMPLE_DOC)
    _write_decision(tmp_path, "D-old-2026-06-10.md", _MINIMAL_DOC)

    with patch("devlab.claudecode.cc_nightly_palace_updates._DECISIONS_DIR", tmp_path):
        docs = scan_decision_docs(all_docs=True)

    assert len(docs) == 2


def test_scan_returns_empty_when_directory_missing(tmp_path):
    missing = tmp_path / "nonexistent"
    with patch("devlab.claudecode.cc_nightly_palace_updates._DECISIONS_DIR", missing):
        docs = scan_decision_docs()
    assert docs == []


def test_scan_skips_unparseable_docs(tmp_path):
    _write_decision(tmp_path, "D-valid-2026-06-13.md", _SAMPLE_DOC)
    _write_decision(tmp_path, "D-invalid.md", _INVALID_DOC)

    with patch("devlab.claudecode.cc_nightly_palace_updates._DECISIONS_DIR", tmp_path):
        docs = scan_decision_docs(date_filter="2026-06-13")

    assert len(docs) == 1


# ── write_decision_nodes ──────────────────────────────────────────────────────

def test_write_decision_nodes_dry_run_returns_count(tmp_path, capsys):
    path = _write_decision(tmp_path, "D-test-2026-06-13.md", _SAMPLE_DOC)
    doc = _parse_decision_doc(path)
    assert doc is not None

    count = write_decision_nodes([doc], dry_run=True)
    out = capsys.readouterr().out

    assert count == 1
    assert "[DRY RUN]" in out
    assert "palace.decisions." in out


def test_write_decision_nodes_calls_db_upsert(tmp_path):
    path = _write_decision(tmp_path, "D-test-2026-06-13.md", _SAMPLE_DOC)
    doc = _parse_decision_doc(path)
    assert doc is not None

    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: s
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("psycopg2.connect", return_value=mock_conn):
        count = write_decision_nodes([doc], dry_run=False)

    assert count == 1
    mock_conn.commit.assert_called_once()


def test_write_decision_nodes_handles_db_failure_gracefully(tmp_path, capsys):
    path = _write_decision(tmp_path, "D-test-2026-06-13.md", _SAMPLE_DOC)
    doc = _parse_decision_doc(path)
    assert doc is not None

    with patch("psycopg2.connect", side_effect=Exception("DB down")):
        count = write_decision_nodes([doc], dry_run=False)

    assert count == 0  # failed but did not raise
    err = capsys.readouterr().err
    assert "palace write failed" in err


# ── write_session_brief ───────────────────────────────────────────────────────

def test_write_session_brief_dry_run(capsys):
    result = write_session_brief("2026-06-13", decision_count=3, dry_run=True)
    out = capsys.readouterr().out
    assert result is True
    assert "[DRY RUN]" in out


def test_write_session_brief_reads_slate_done(tmp_path):
    slate_dir = tmp_path / "slates"
    slate_dir.mkdir()
    slate = slate_dir / "20260613.slate.txt"
    slate.write_text(
        "# Slate 2026-06-13\n## Notes\n\n## Done today\n- T-x: did something\n\n## Planned\n",
        encoding="utf-8",
    )

    done = _read_slate_done("2026-06-13")

    assert "T-x: did something" in done


def test_write_session_brief_calls_db(tmp_path):
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: s
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("psycopg2.connect", return_value=mock_conn), \
         patch("devlab.claudecode.cc_nightly_palace_updates._IGOR_HOME", tmp_path):
        result = write_session_brief("2026-06-13", decision_count=2, dry_run=False)

    assert result is True
    mock_conn.commit.assert_called_once()


# ── run (end-to-end dry run) ──────────────────────────────────────────────────

def test_run_dry_run_returns_summary(tmp_path):
    _write_decision(tmp_path, "D-sample-2026-06-13.md", _SAMPLE_DOC)

    with patch("devlab.claudecode.cc_nightly_palace_updates._DECISIONS_DIR", tmp_path), \
         patch("devlab.claudecode.cc_nightly_palace_updates._IGOR_HOME", tmp_path):
        summary = run(date="2026-06-13", dry_run=True)

    assert "decisions_found" in summary
    assert "decisions_written" in summary
    assert "session_brief_written" in summary
    assert summary["decisions_found"] == 1
    assert summary["decisions_written"] == 1
    assert summary["session_brief_written"] is True

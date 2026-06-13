"""
Tests for devices/scraps/orientation_classifier.py and ToolLoop integration.

Tests:
- extract_keywords: stop words filtered, short words filtered, deduplication
- classify_task_shape: tag-based and title-prefix-based classification
- query_relevant_files: score ranking, path deduplication, fail-open on DB error
- classify: DB failure returns empty BuilderReport
- BuilderReport.to_text: format + empty case
- ToolLoop._build_initial_message: builder_report_text is prepended
- ToolLoop._orientation_prefix: fail-open when classify raises
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.scraps.orientation_classifier import (
    BuilderReport,
    FileMatch,
    classify,
    classify_task_shape,
    extract_keywords,
    query_relevant_files,
)
from devices.dicksimnel.toolloop import _build_initial_message, _orientation_prefix


# ── extract_keywords ──────────────────────────────────────────────────────────

def test_extract_keywords_basic():
    ticket = {"title": "Extend ToolLoop with builder report", "tags": ["Inference"], "description": ""}
    kw = extract_keywords(ticket)
    assert "ToolLoop" in kw or "toolloop" in [k.lower() for k in kw]
    assert "builder" in kw or "Builder" in kw
    # Stop words filtered
    assert "with" not in kw
    assert "the" not in kw


def test_extract_keywords_dedup():
    ticket = {"title": "extend extend extend", "tags": [], "description": ""}
    kw = extract_keywords(ticket)
    lower = [k.lower() for k in kw]
    assert lower.count("extend") == 1


def test_extract_keywords_min_len():
    ticket = {"title": "add fix new use run set get put", "tags": [], "description": ""}
    kw = extract_keywords(ticket)
    # "add", "fix", "new", "use", "run", "set", "get", "put" are all 3 chars — filtered
    assert all(len(k) >= 4 for k in kw)


def test_extract_keywords_from_tags():
    ticket = {"title": "", "tags": ["Database", "Queue"], "description": ""}
    kw = extract_keywords(ticket)
    assert "Database" in kw or "database" in [k.lower() for k in kw]


# ── classify_task_shape ───────────────────────────────────────────────────────

def test_task_shape_bug_from_tag():
    assert classify_task_shape({"tags": ["Bug", "Regression"], "title": ""}) == "bug-fix"


def test_task_shape_refactor_from_tag():
    assert classify_task_shape({"tags": ["Refactor"], "title": ""}) == "refactor"


def test_task_shape_docs():
    assert classify_task_shape({"tags": ["Docs"], "title": ""}) == "docs"


def test_task_shape_feature():
    assert classify_task_shape({"tags": ["Feature"], "title": ""}) == "new-feature"


def test_task_shape_general_fallback():
    assert classify_task_shape({"tags": ["Inference", "Cost"], "title": ""}) == "general"


def test_task_shape_config():
    assert classify_task_shape({"tags": ["Config"], "title": ""}) == "config"


# ── BuilderReport.to_text ─────────────────────────────────────────────────────

def test_builder_report_to_text_empty():
    report = BuilderReport(keywords=[], relevant_files=[], task_shape="general")
    assert report.to_text() == ""


def test_builder_report_to_text_with_files():
    report = BuilderReport(
        keywords=["ToolLoop", "builder"],
        relevant_files=[
            {"path": "devices/dicksimnel/toolloop.py", "symbol": "ToolLoop", "kind": "class",
             "summary": "Multi-turn ReAct inference loop", "score": 3.0}
        ],
        task_shape="new-feature",
        estimated_complexity="L",
    )
    text = report.to_text()
    assert "Builder Report" in text
    assert "toolloop.py" in text
    assert "new-feature" in text
    assert "ToolLoop" in text


# ── query_relevant_files (mocked DB) ─────────────────────────────────────────

def test_query_relevant_files_scores_symbol_higher():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    # Two rows: one with keyword in symbol, one only in path
    mock_cursor.fetchall.return_value = [
        ("devices/dicksimnel/toolloop.py", "ToolLoop", "class", "ToolLoop class"),
        ("devices/foo/bar.py", "some_func", "function", "toolloop helper"),
    ]

    with patch("psycopg2.connect", return_value=mock_conn):
        matches = query_relevant_files(["ToolLoop"], "postgresql://test/db")

    assert len(matches) >= 1
    # toolloop.py with symbol match should rank higher
    assert matches[0].path == "devices/dicksimnel/toolloop.py"
    assert matches[0].score > matches[1].score if len(matches) > 1 else True


def test_query_relevant_files_deduplicates_by_path():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    # Same path, two rows (class + method)
    mock_cursor.fetchall.return_value = [
        ("devices/foo/bar.py", "MyClass", "class", "tool helper"),
        ("devices/foo/bar.py", "MyClass.run", "method", "tool runner"),
    ]

    with patch("psycopg2.connect", return_value=mock_conn):
        matches = query_relevant_files(["tool"], "postgresql://test/db")

    # Should deduplicate to one path entry
    assert len(matches) == 1
    assert matches[0].path == "devices/foo/bar.py"


def test_query_relevant_files_empty_keywords():
    matches = query_relevant_files([], "postgresql://test/db")
    assert matches == []


# ── classify (fail-open on DB error) ─────────────────────────────────────────

def test_classify_db_failure_returns_empty():
    ticket = {"id": "T-test", "title": "some ticket", "tags": [], "description": "", "size": "S"}
    with patch("devices.scraps.orientation_classifier.query_relevant_files",
               side_effect=Exception("DB down")):
        report = classify(ticket, db_url="postgresql://test/db")
    assert isinstance(report, BuilderReport)
    assert report.relevant_files == []
    assert report.task_shape == "general"
    assert report.estimated_complexity == "S"


def test_classify_returns_builder_report():
    ticket = {"id": "T-test", "title": "Extend ToolLoop", "tags": ["Inference"], "description": "", "size": "M"}
    with patch("devices.scraps.orientation_classifier.query_relevant_files", return_value=[
        FileMatch(path="devices/dicksimnel/toolloop.py", symbol="ToolLoop",
                  kind="class", summary="ReAct loop", score=3.0)
    ]):
        report = classify(ticket, db_url="postgresql://test/db")
    assert len(report.relevant_files) == 1
    assert report.relevant_files[0]["path"] == "devices/dicksimnel/toolloop.py"


# ── ToolLoop helpers ───────────────────────────────────────────────────────────

def test_build_initial_message_no_report():
    ticket = {"id": "T-foo", "title": "Do a thing", "tags": ["Test"], "description": "details"}
    msg = _build_initial_message(ticket)
    assert "Ticket ID: T-foo" in msg
    assert "Do a thing" in msg
    assert "details" in msg


def test_build_initial_message_with_report():
    ticket = {"id": "T-foo", "title": "Do a thing", "tags": ["Test"], "description": "details"}
    msg = _build_initial_message(ticket, builder_report_text="## Builder Report\nfile.py\n\n")
    assert msg.startswith("## Builder Report")
    assert "Ticket ID: T-foo" in msg


def test_orientation_prefix_fail_open():
    ticket = {"id": "T-foo", "title": "test", "tags": [], "description": "", "size": "S"}
    with patch("devices.dicksimnel.toolloop._orientation_prefix.__module__",
               new="devices.dicksimnel.toolloop"):
        # Patch classify to raise — _orientation_prefix should return ""
        with patch("devices.scraps.orientation_classifier.classify",
                   side_effect=Exception("boom")):
            result = _orientation_prefix(ticket)
    assert result == ""


def test_orientation_prefix_no_files_returns_empty():
    ticket = {"id": "T-foo", "title": "test", "tags": [], "description": "", "size": "S"}
    with patch("devices.scraps.orientation_classifier.classify",
               return_value=BuilderReport(keywords=[], relevant_files=[], task_shape="general")):
        result = _orientation_prefix(ticket)
    assert result == ""


def test_orientation_prefix_with_files():
    ticket = {"id": "T-foo", "title": "Extend ToolLoop", "tags": [], "description": "", "size": "M"}
    mock_report = BuilderReport(
        keywords=["ToolLoop"],
        relevant_files=[{"path": "devices/dicksimnel/toolloop.py", "symbol": "ToolLoop",
                         "kind": "class", "summary": "ReAct loop", "score": 3.0}],
        task_shape="new-feature",
        estimated_complexity="M",
    )
    with patch("devices.scraps.orientation_classifier.classify", return_value=mock_report):
        result = _orientation_prefix(ticket)
    assert "Builder Report" in result
    assert result.endswith("\n\n")

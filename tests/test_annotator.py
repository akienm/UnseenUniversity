"""
Tests for devices/classifier/annotator.py.

Tests:
- _path_to_dotted: correct palace node ID from file path
- _fallback_signature: class+function-based summary
- _haiku_problem_signature: falls back on HTTP error
- run_annotator: dry-run returns correct counts
- run_annotator: upserts rows with problem_signature to clan.memories
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.classifier.annotator import (
    ModuleInfo,
    _fallback_signature,
    _path_to_dotted,
    run_annotator,
)


# ── _path_to_dotted ────────────────────────────────────────────────────────────

def test_path_to_dotted_simple():
    assert _path_to_dotted("devices/granny/daemon.py") == (
        "palace.codebase.unseen_university.devices.granny.daemon"
    )


def test_path_to_dotted_nested():
    assert _path_to_dotted("devices/discord_bot/bot.py") == (
        "palace.codebase.unseen_university.devices.discord_bot.bot"
    )


def test_path_to_dotted_top_level():
    assert _path_to_dotted("config/cc_env.sh") == (
        "palace.codebase.unseen_university.config.cc_env"
    )


# ── _fallback_signature ────────────────────────────────────────────────────────

def test_fallback_with_classes_and_functions():
    symbols = [
        {"symbol": "MyClass", "kind": "class", "summary": "A class"},
        {"symbol": "my_func", "kind": "function", "summary": "A function"},
    ]
    sig = _fallback_signature("devices/foo/bar.py", symbols)
    assert "MyClass" in sig
    assert "my_func" in sig


def test_fallback_no_symbols_uses_stem():
    sig = _fallback_signature("devices/foo/bar.py", [])
    assert "bar" in sig


def test_fallback_classes_only():
    symbols = [{"symbol": "A", "kind": "class", "summary": "class A"}]
    sig = _fallback_signature("x.py", symbols)
    assert "A" in sig
    assert sig.startswith("handles:")


# ── _haiku_problem_signature — fallback on HTTP error ─────────────────────────

def test_haiku_falls_back_on_error():
    from unseen_university.devices.classifier.annotator import _haiku_problem_signature
    symbols = [{"symbol": "foo", "kind": "function", "summary": "does foo"}]

    with patch("unseen_university.devices.classifier.annotator._OR_API_KEY", "fake-key"), \
         patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
        sig = _haiku_problem_signature("x.py", symbols)

    # Should return a fallback signature (not raise)
    assert isinstance(sig, str)
    assert len(sig) > 0


def test_haiku_skips_when_no_api_key():
    from unseen_university.devices.classifier.annotator import _haiku_problem_signature
    symbols = [{"symbol": "foo", "kind": "function", "summary": "does foo"}]

    with patch("unseen_university.devices.classifier.annotator._OR_API_KEY", ""):
        sig = _haiku_problem_signature("x.py", symbols)

    assert isinstance(sig, str)
    assert len(sig) > 0


# ── run_annotator (mocked DB) ──────────────────────────────────────────────────

def _make_mock_rows(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        rows.append({
            "path": p,
            "symbol": "SomeClass",
            "kind": "class",
            "summary": f"class in {p}",
        })
    return rows


def _psycopg2_mock(rows):
    """Build a minimal psycopg2 connection mock that returns the given rows."""
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = rows
    mock_cursor.fetchone.return_value = None  # no existing rows

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn


def test_run_annotator_dry_run():
    with patch("unseen_university.devices.classifier.annotator._query_modules", return_value=[
        ModuleInfo(
            path="devices/foo/bar.py",
            dotted_id="palace.codebase.unseen_university.devices.foo.bar",
            symbols=[{"symbol": "X", "kind": "class", "summary": "X"}],
        )
    ]):
        result = run_annotator(dry_run=True)
    assert result["modules"] == 1
    assert result["inserted"] == 0
    assert result["updated"] == 0
    assert result["errors"] == 0


def test_run_annotator_inserts_rows():
    mod = ModuleInfo(
        path="devices/foo/bar.py",
        dotted_id="palace.codebase.unseen_university.devices.foo.bar",
        symbols=[{"symbol": "X", "kind": "class", "summary": "X handles routing"}],
    )

    with patch("unseen_university.devices.classifier.annotator._query_modules", return_value=[mod]), \
         patch("unseen_university.devices.classifier.annotator._haiku_problem_signature",
               return_value="handles: X routing"), \
         patch("unseen_university.devices.classifier.annotator._upsert_memory", return_value="inserted") as mock_up, \
         patch("psycopg2.connect", return_value=MagicMock()):
        result = run_annotator(mode="full_build")

    assert result["inserted"] == 1
    assert result["updated"] == 0
    assert result["errors"] == 0
    mock_up.assert_called_once()


def test_run_annotator_db_connect_failure():
    with patch("unseen_university.devices.classifier.annotator._query_modules", return_value=[
        ModuleInfo(
            path="x.py",
            dotted_id="palace.codebase.unseen_university.x",
            symbols=[],
        )
    ]), patch("psycopg2.connect", side_effect=Exception("db down")):
        result = run_annotator()

    assert result["errors"] == 1


def test_run_annotator_nightly_mode_no_modules():
    with patch("unseen_university.devices.classifier.annotator._query_modules", return_value=[]):
        result = run_annotator(mode="nightly")
    assert result["modules"] == 0
    assert result["errors"] == 0

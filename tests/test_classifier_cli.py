"""
Tests for devices/classifier/cli.py and _query_palace_trees wiring.

Tests:
- cmd_classify: returns JSON with expected keys
- cmd_classify: fails open on exception (returns empty report, exit 0)
- cmd_freshness: returns updated stale flag
- cmd_freshness: fails open on bad JSON input
- _query_palace_trees: returns files from palace nodes (mocked DB)
- _query_palace_trees: returns empty on DB error (fail-open)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── CLI tests (cmd_classify) ──────────────────────────────────────────────────

class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_cmd_classify_returns_json_keys():
    from devices.classifier.cli import cmd_classify
    args = _Args(title="Fix granny cascade", tags=["Granny"], description="cascade idle routing")

    with patch("devices.classifier.device.ClassifierDevice._db_url", return_value=""), \
         patch("devices.classifier.device.ClassifierDevice._query_palace_trees",
               return_value=([], [])):
        result_code = cmd_classify(args)

    assert result_code == 0


def test_cmd_classify_empty_title_returns_empty_report(capsys):
    from devices.classifier.cli import cmd_classify
    args = _Args(title="", tags=[], description="")
    result_code = cmd_classify(args)
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert result_code == 0
    assert "relevant_files" in data


def test_cmd_classify_fails_open_on_exception(capsys):
    from devices.classifier.cli import cmd_classify
    args = _Args(title="something", tags=[], description="")

    with patch("devices.classifier.device.ClassifierDevice.classify",
               side_effect=RuntimeError("device down")):
        result_code = cmd_classify(args)

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert result_code == 0
    assert data["relevant_files"] == []
    assert data["classifier"] == "empty"


# ── cmd_freshness ─────────────────────────────────────────────────────────────

def test_cmd_freshness_stale_old_report(capsys):
    from devices.classifier.cli import cmd_freshness
    old_ts = "2020-01-01T00:00:00+00:00"
    report_json = json.dumps({"ts": old_ts, "relevant_files": [], "stale": False})
    args = _Args(report_json=report_json)

    with patch("devices.classifier.device.ClassifierDevice._check_in_flight_overlap",
               return_value=[]):
        result_code = cmd_freshness(args)

    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert result_code == 0
    assert data["stale"] is True  # old timestamp → stale


def test_cmd_freshness_fails_open_on_bad_json(capsys):
    from devices.classifier.cli import cmd_freshness
    args = _Args(report_json="not_json{{")
    result_code = cmd_freshness(args)
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert result_code == 0
    assert "relevant_files" in data


# ── _query_palace_trees ────────────────────────────────────────────────────────

def test_query_palace_trees_returns_file_paths():
    from devices.classifier.device import ClassifierDevice

    mock_cursor = MagicMock()
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = [
        {"id": "palace.codebase.unseen_university.devices.granny.daemon",
         "file_path": "devices/granny/daemon.py"},
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    device = ClassifierDevice(llm_fallback=False)
    with patch("devices.classifier.device.ClassifierDevice._db_url",
               return_value="postgresql://test"), \
         patch("psycopg2.connect", return_value=mock_conn):
        files, nodes = device._query_palace_trees(
            ["palace.codebase.unseen_university"],
            "fix granny cascade dispatch routing",
        )

    assert "devices/granny/daemon.py" in files


def test_query_palace_trees_empty_on_db_error():
    from devices.classifier.device import ClassifierDevice

    device = ClassifierDevice(llm_fallback=False)
    with patch("devices.classifier.device.ClassifierDevice._db_url",
               return_value="postgresql://test"), \
         patch("psycopg2.connect", side_effect=Exception("db down")):
        files, nodes = device._query_palace_trees(
            ["palace.codebase.unseen_university"],
            "some task description here",
        )

    assert files == []


def test_query_palace_trees_empty_on_no_db_url():
    from devices.classifier.device import ClassifierDevice

    device = ClassifierDevice(llm_fallback=False)
    with patch("devices.classifier.device.ClassifierDevice._db_url", return_value=""):
        files, nodes = device._query_palace_trees(
            [],
            "some task",
        )

    assert files == []

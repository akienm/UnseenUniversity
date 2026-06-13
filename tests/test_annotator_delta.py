"""
Tests for annotator delta update on ticket close.

Tests:
- run_annotator with file_paths: processes only those files
- run_annotator file_paths=[]: returns empty immediately
- _annotator_delta_update in cc_queue: calls annotator with git diff files
- _annotator_delta_update: non-fatal when git fails
- _annotator_delta_update: non-fatal when annotator fails
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.classifier.annotator import ModuleInfo, run_annotator


# ── run_annotator delta (file_paths) ──────────────────────────────────────────

def test_run_annotator_file_paths_targeted():
    """run_annotator with file_paths processes exactly those files."""
    mod = ModuleInfo(
        path="devices/granny/daemon.py",
        dotted_id="palace.codebase.unseen_university.devices.granny.daemon",
        symbols=[{"symbol": "run_once", "kind": "function", "summary": "main loop"}],
    )

    with patch("devices.classifier.annotator._query_modules", return_value=[mod]) as mock_qm, \
         patch("devices.classifier.annotator._haiku_problem_signature", return_value="handles: loop"), \
         patch("devices.classifier.annotator._upsert_memory", return_value="updated"), \
         patch("psycopg2.connect", return_value=MagicMock()):
        result = run_annotator(file_paths=["devices/granny/daemon.py"])

    # _query_modules must have been called with file_paths set
    mock_qm.assert_called_once()
    assert mock_qm.call_args[1].get("file_paths") == ["devices/granny/daemon.py"]
    assert result["modules"] == 1
    assert result["updated"] == 1


def test_run_annotator_file_paths_empty():
    """run_annotator with file_paths=[] returns immediately with zero counts."""
    with patch("devices.classifier.annotator._query_modules", return_value=[]) as mock_qm:
        result = run_annotator(file_paths=[])

    mock_qm.assert_called_once()
    assert result["modules"] == 0
    assert result["errors"] == 0


# ── _annotator_delta_update in cc_queue ───────────────────────────────────────

def test_annotator_delta_update_calls_annotator():
    """_annotator_delta_update gets git diff and passes touched files to run_annotator."""
    from lab.claudecode.cc_queue import _annotator_delta_update

    mock_git_result = MagicMock()
    mock_git_result.returncode = 0
    mock_git_result.stdout = "devices/granny/daemon.py\nskills/sprint-ticket/SKILL.md\n"

    with patch("subprocess.run", return_value=mock_git_result) as mock_run, \
         patch("devices.classifier.annotator.run_annotator",
               return_value={"modules": 2, "inserted": 0, "updated": 2, "errors": 0}) as mock_ann:
        _annotator_delta_update("T-test-ticket")

    mock_ann.assert_called_once()
    call_kwargs = mock_ann.call_args[1]
    assert "devices/granny/daemon.py" in call_kwargs["file_paths"]


def test_annotator_delta_update_nonfatal_on_git_error():
    """_annotator_delta_update does not raise when git fails."""
    from lab.claudecode.cc_queue import _annotator_delta_update

    with patch("subprocess.run", side_effect=Exception("git not found")):
        _annotator_delta_update("T-test")  # must not raise


def test_annotator_delta_update_nonfatal_on_annotator_error():
    """_annotator_delta_update does not raise when annotator fails."""
    from lab.claudecode.cc_queue import _annotator_delta_update

    mock_git = MagicMock()
    mock_git.returncode = 0
    mock_git.stdout = "devices/foo/bar.py\n"

    with patch("subprocess.run", return_value=mock_git), \
         patch("devices.classifier.annotator.run_annotator",
               side_effect=RuntimeError("annotator crashed")):
        _annotator_delta_update("T-test")  # must not raise


def test_annotator_delta_update_skips_empty_git_diff():
    """_annotator_delta_update exits early when git diff is empty."""
    from lab.claudecode.cc_queue import _annotator_delta_update

    mock_git = MagicMock()
    mock_git.returncode = 0
    mock_git.stdout = ""

    with patch("subprocess.run", return_value=mock_git), \
         patch("devices.classifier.annotator.run_annotator") as mock_ann:
        _annotator_delta_update("T-test")

    mock_ann.assert_not_called()

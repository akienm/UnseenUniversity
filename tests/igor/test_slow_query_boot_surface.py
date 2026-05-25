"""T-slow-query-boot-surface: boot_surface_slow_queries pushes top offenders
to ring_memory instead of keeping them only on-disk.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.tools import slow_query as sq


def _fixture_log(tmp_path: Path) -> Path:
    log = tmp_path / "db_queries.log"
    log.write_text(
        "\n".join(
            [
                "2026-04-22 10:00:00 owner=x turn=? elapsed=800ms sql=SELECT slow_a FROM t",
                "2026-04-22 10:00:01 owner=x turn=? elapsed=100ms sql=SELECT slow_a FROM t",
                "2026-04-22 10:00:02 owner=x turn=? elapsed=5000ms sql=UPDATE hot_row",
                "2026-04-22 10:00:03 owner=x turn=? elapsed=60ms sql=SELECT minor FROM t",
            ]
        )
        + "\n"
    )
    return log


def test_pushes_report_to_ring_when_log_has_entries(monkeypatch, tmp_path):
    log = _fixture_log(tmp_path)
    monkeypatch.setattr(sq, "_LOG_PATH", log)

    cortex = MagicMock()
    sq.boot_surface_slow_queries(cortex, top_n=3)

    cortex.write_ring.assert_called_once()
    content, kwargs = cortex.write_ring.call_args[0], cortex.write_ring.call_args[1]
    report = content[0]
    assert "SLOW_QUERY_REPORT|total=4" in report
    assert "UPDATE hot_row" in report
    assert "SELECT slow_a" in report
    assert kwargs.get("category") == "db_diagnostic"


def test_silent_when_log_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sq, "_LOG_PATH", tmp_path / "does_not_exist.log")
    cortex = MagicMock()
    sq.boot_surface_slow_queries(cortex, top_n=5)
    cortex.write_ring.assert_not_called()


def test_silent_when_log_has_no_parseable_lines(monkeypatch, tmp_path):
    log = tmp_path / "db_queries.log"
    log.write_text("garbage\nmore garbage without elapsed\n")
    monkeypatch.setattr(sq, "_LOG_PATH", log)

    cortex = MagicMock()
    sq.boot_surface_slow_queries(cortex, top_n=5)
    cortex.write_ring.assert_not_called()


def test_exception_in_ring_write_does_not_propagate(monkeypatch, tmp_path):
    """Boot must not fail on diagnostics errors."""
    log = _fixture_log(tmp_path)
    monkeypatch.setattr(sq, "_LOG_PATH", log)

    cortex = MagicMock()
    cortex.write_ring.side_effect = RuntimeError("DB down")

    sq.boot_surface_slow_queries(cortex, top_n=3)


def test_top_n_limits_output(monkeypatch, tmp_path):
    """With 4 distinct patterns and top_n=2, only 2 patterns appear in the report."""
    log = _fixture_log(tmp_path)
    monkeypatch.setattr(sq, "_LOG_PATH", log)

    cortex = MagicMock()
    sq.boot_surface_slow_queries(cortex, top_n=2)

    report = cortex.write_ring.call_args[0][0]
    assert "UPDATE hot_row" in report
    assert "SELECT slow_a" in report
    assert "SELECT minor" not in report

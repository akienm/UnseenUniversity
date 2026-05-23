"""Tests for Stopwatch and CSV rolling log."""

from __future__ import annotations

import csv
import time
from pathlib import Path

import pytest

from diagnostic_base.perf import Stopwatch, timed, _csv_path, _prune_old_csvs


@pytest.fixture()
def tmp_log_root(tmp_path):
    return tmp_path / "datacenter_logs"


class TestStopwatch:
    def test_context_manager_records_elapsed(self, tmp_log_root):
        sw = Stopwatch("test_op", device_id="dev", log_root=tmp_log_root)
        with sw:
            time.sleep(0.01)
        assert sw.elapsed_s is not None
        assert sw.elapsed_s >= 0.01
        assert sw.success is True

    def test_exception_sets_success_false(self, tmp_log_root):
        sw = Stopwatch("test_fail", device_id="dev", log_root=tmp_log_root)
        with pytest.raises(ValueError):
            with sw:
                raise ValueError("boom")
        assert sw.success is False

    def test_explicit_start_stop(self, tmp_log_root):
        sw = Stopwatch("explicit", device_id="dev", log_root=tmp_log_root)
        sw.start()
        time.sleep(0.005)
        sw.stop(success=True)
        assert sw.elapsed_s >= 0.005

    def test_stop_without_start_raises(self, tmp_log_root):
        sw = Stopwatch("no_start", device_id="dev", log_root=tmp_log_root)
        with pytest.raises(RuntimeError):
            sw.stop()

    def test_csv_written_after_stop(self, tmp_log_root):
        sw = Stopwatch(
            "csv_test", device_id="mydev", class_name="MyClass", log_root=tmp_log_root
        )
        with sw:
            pass
        perf_dir = tmp_log_root / "mydev" / "perf"
        csv_files = list(perf_dir.glob("*.perf.csv"))
        assert len(csv_files) == 1
        rows = list(csv.DictReader(csv_files[0].open()))
        assert len(rows) == 1
        assert rows[0]["stopwatch_id"] == "csv_test"
        assert rows[0]["class_name"] == "MyClass"
        assert rows[0]["device_id"] == "mydev"
        assert rows[0]["success"] == "True"

    def test_csv_appends_multiple_rows(self, tmp_log_root):
        for i in range(3):
            sw = Stopwatch(f"op_{i}", device_id="mydev", log_root=tmp_log_root)
            with sw:
                pass
        perf_dir = tmp_log_root / "mydev" / "perf"
        csv_files = list(perf_dir.glob("*.perf.csv"))
        rows = list(csv.DictReader(csv_files[0].open()))
        assert len(rows) == 3

    def test_timed_context_manager(self, tmp_log_root):
        with timed("named_op", device_id="d", log_root=tmp_log_root) as sw:
            pass
        assert sw.success is True
        assert sw.elapsed_s is not None


class TestPruning:
    def test_prune_removes_old_files(self, tmp_path):
        perf_dir = tmp_path / "perf"
        perf_dir.mkdir()
        old = perf_dir / "2020-01-01.perf.csv"
        old.write_text("ts_start\n2020-01-01\n")
        # backdate mtime to > 10 days ago
        import os

        old_time = time.time() - 11 * 86400
        os.utime(old, (old_time, old_time))
        _prune_old_csvs(perf_dir, keep_days=10)
        assert not old.exists()

    def test_prune_keeps_recent_files(self, tmp_path):
        perf_dir = tmp_path / "perf"
        perf_dir.mkdir()
        recent = perf_dir / "2099-12-31.perf.csv"
        recent.write_text("ts_start\n2099-01-01\n")
        _prune_old_csvs(perf_dir, keep_days=10)
        assert recent.exists()

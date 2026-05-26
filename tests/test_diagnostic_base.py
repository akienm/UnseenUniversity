"""Tests for DiagnosticBase."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from diagnostic_base.base import DiagnosticBase, _SafeDict

# Module-level instance — gc trick works because module dict is a real heap dict
_module_device = DiagnosticBase(device_id="mod", name="module_device")


class TestInstanceNaming:
    def test_explicit_name_wins(self):
        obj = DiagnosticBase(device_id="d", name="my_sensor")
        assert obj.get_name() == "my_sensor"

    def test_gc_trick_finds_module_scope_name(self):
        # gc.get_referrers works for module/class-scope dicts, not CPython fast-locals
        assert _module_device.get_name() == "module_device"

    def test_fallback_when_no_name(self):
        # Function-local vars aren't in a heap dict; falls back to class name
        obj = DiagnosticBase(device_id="d")
        name = obj.get_name()
        assert isinstance(name, str)
        assert len(name) > 0

    def test_get_name_hierarchical(self):
        parent = DiagnosticBase(device_id="p", name="parent")
        child = DiagnosticBase(device_id="c", name="child", parent=parent)
        assert child.get_name() == "parent.child"

    def test_str_includes_class_and_name(self):
        obj = DiagnosticBase(device_id="x")
        s = str(obj)
        assert "DiagnosticBase" in s
        assert "name=" in s


class TestSubstitution:
    def test_simple_substitution(self):
        result = DiagnosticBase.resolve_substitutions(
            "{greeting} world", {"greeting": "hello"}
        )
        assert result == "hello world"

    def test_nested_substitution(self):
        ctx = {"a": "{b}", "b": "resolved"}
        result = DiagnosticBase.resolve_substitutions("{a}", ctx)
        assert result == "resolved"

    def test_unknown_key_left_intact(self):
        result = DiagnosticBase.resolve_substitutions("{unknown}", {})
        assert result == "{unknown}"

    def test_safe_dict_missing(self):
        d = _SafeDict({"x": "1"})
        assert d["x"] == "1"
        assert d["missing"] == "{missing}"


class TestApplyKwargs:
    def test_kwargs_become_attributes(self):
        obj = DiagnosticBase(device_id="d", foo="bar", count=42)
        assert obj.foo == "bar"
        assert obj.count == 42


class TestDump:
    def test_dump_returns_public_attrs(self):
        obj = DiagnosticBase(device_id="d", color="blue")
        d = obj.dump()
        assert "color" in d
        # private attrs excluded
        assert "_device_id" not in d

    def test_bannerize_contains_name(self):
        obj = DiagnosticBase(device_id="d")
        banner = obj.bannerize()
        assert "DiagnosticBase" in banner


class TestTimestamp:
    def test_get_timestamp_format(self):
        ts = DiagnosticBase.get_timestamp()
        assert "T" in ts
        assert ts.endswith("Z")

    def test_elapsed_s_increases(self):
        obj = DiagnosticBase(device_id="d")
        time.sleep(0.01)
        assert obj.elapsed_s() >= 0.01

    def test_timeout_remaining_positive(self):
        obj = DiagnosticBase(device_id="d")
        remaining = obj.timeout_remaining(10.0)
        assert 0 < remaining <= 10.0

    def test_timeout_remaining_negative_after_expiry(self):
        obj = DiagnosticBase(device_id="d")
        time.sleep(0.05)
        assert obj.timeout_remaining(0.01) < 0


class TestContextManager:
    def test_context_manager_returns_self(self):
        obj = DiagnosticBase(device_id="d")
        with obj as ctx:
            assert ctx is obj

    def test_context_manager_does_not_suppress_exceptions(self):
        obj = DiagnosticBase(device_id="d")
        with pytest.raises(RuntimeError):
            with obj:
                raise RuntimeError("boom")


class TestLogger:
    def test_logger_is_tagged_logger(self):
        from diagnostic_base.tagged_logger import TaggedLogger

        obj = DiagnosticBase(device_id="d")
        assert isinstance(obj.logger, TaggedLogger)

    def test_convenience_methods_do_not_raise(self):
        obj = DiagnosticBase(device_id="d")
        obj.debug("d")
        obj.info("i")
        obj.warning("w")
        obj.error("e")

    def test_stopwatch_factory(self, tmp_path):
        obj = DiagnosticBase(device_id="mydev")
        obj._log_root = tmp_path
        sw = obj.stopwatch("op", comment="test")
        assert sw.device_id == "mydev"
        assert sw.stopwatch_id == "op"
        assert sw.comment == "test"


class TestJsonLogSink:
    def test_info_writes_json_file(self, tmp_path):
        """Each log call produces one JSON file in <log_root>/<device_id>/log/json/."""
        import time as _time
        from diagnostic_base.base import _logger_cache

        class _TestDevice(DiagnosticBase):
            _log_root = tmp_path

        # Clear cache so new binding picks up tmp_path as log_root
        _logger_cache.pop(_TestDevice, None)
        obj = _TestDevice(device_id="testdev")
        obj.info("hello from test")
        _time.sleep(0.05)  # let loguru flush (enqueue=False so near-instant)

        out_dir = tmp_path / "testdev" / "log" / "json"
        files = list(out_dir.glob("*.json"))
        assert len(files) >= 1, f"expected JSON file in {out_dir}, got none"

        payload = json.loads(files[0].read_text())
        assert payload["device_id"] == "testdev"
        assert payload["level"] == "INFO"
        assert "hello from test" in payload["message"]

    def test_filename_encodes_timestamp_and_level(self, tmp_path):
        """Filename contains YYYYMMDD-HHMMSS, logger name, and level."""
        import re
        from diagnostic_base.base import _logger_cache

        class _Dev2(DiagnosticBase):
            _log_root = tmp_path

        _logger_cache.pop(_Dev2, None)
        obj = _Dev2(device_id="dev2")
        obj.warning("test warning")

        import time as _time

        _time.sleep(0.05)
        out_dir = tmp_path / "dev2" / "log" / "json"
        files = list(out_dir.glob("*_warning.json"))
        assert files, "expected a *_warning.json file"
        assert re.match(r"\d{8}-\d{6}-\d{6}_", files[0].name)

    def test_prune_json_logs_removes_old_files(self, tmp_path):
        """prune_json_logs deletes files older than the retention window."""
        import time as _time
        from diagnostic_base.base import prune_json_logs

        out_dir = tmp_path / "dev" / "log" / "json"
        out_dir.mkdir(parents=True)
        old_file = out_dir / "old.json"
        old_file.write_text("{}")
        # Back-date to 31 days ago
        old_ts = _time.time() - 31 * 86400
        import os

        os.utime(old_file, (old_ts, old_ts))

        new_file = out_dir / "new.json"
        new_file.write_text("{}")

        deleted = prune_json_logs(log_root=tmp_path, days=30)
        assert deleted == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_prune_removes_old_trace_files(self, tmp_path):
        """prune_json_logs also prunes trace/*.jsonl files older than retention."""
        import os
        import time as _time
        from diagnostic_base.base import prune_json_logs

        trace_dir = tmp_path / "dev" / "trace"
        trace_dir.mkdir(parents=True)
        old_file = trace_dir / "20260101.jsonl"
        old_file.write_text("{}\n")
        old_ts = _time.time() - 31 * 86400
        os.utime(old_file, (old_ts, old_ts))

        new_file = trace_dir / "20260525.jsonl"
        new_file.write_text("{}\n")

        deleted = prune_json_logs(log_root=tmp_path, days=30)
        assert deleted == 1
        assert not old_file.exists()
        assert new_file.exists()


class TestTrace:
    def test_trace_record_writes_jsonl(self, tmp_path):
        from diagnostic_base.base import _logger_cache

        class _TraceDev(DiagnosticBase):
            _log_root = tmp_path

        _logger_cache.pop(_TraceDev, None)
        obj = _TraceDev(device_id="tracedev")
        obj.trace_record("test_event", {"x": 1})

        trace_dir = tmp_path / "tracedev" / "trace"
        files = list(trace_dir.glob("*.jsonl"))
        assert len(files) == 1
        line = json.loads(files[0].read_text().strip())
        assert line["device"] == "tracedev"
        assert line["event"] == "test_event"
        assert line["data"] == {"x": 1}
        assert "ts" in line

    def test_trace_record_no_data(self, tmp_path):
        from diagnostic_base.base import _logger_cache

        class _TraceDev2(DiagnosticBase):
            _log_root = tmp_path

        _logger_cache.pop(_TraceDev2, None)
        obj = _TraceDev2(device_id="td2")
        obj.trace_record("no_data_event")

        trace_dir = tmp_path / "td2" / "trace"
        line = json.loads(list(trace_dir.glob("*.jsonl"))[0].read_text().strip())
        assert line["event"] == "no_data_event"
        assert "data" not in line

    def test_last_traces_returns_recent_first(self, tmp_path):
        from diagnostic_base.base import _logger_cache

        class _TraceDev3(DiagnosticBase):
            _log_root = tmp_path

        _logger_cache.pop(_TraceDev3, None)
        obj = _TraceDev3(device_id="td3")
        obj.trace_record("alpha")
        obj.trace_record("beta")
        obj.trace_record("gamma")

        traces = obj.last_traces(n=10)
        assert len(traces) == 3
        events = [t["event"] for t in traces]
        assert events[0] == "gamma"
        assert events[-1] == "alpha"

    def test_last_traces_n_cap(self, tmp_path):
        from diagnostic_base.base import _logger_cache

        class _TraceDev4(DiagnosticBase):
            _log_root = tmp_path

        _logger_cache.pop(_TraceDev4, None)
        obj = _TraceDev4(device_id="td4")
        for i in range(10):
            obj.trace_record(f"event_{i}")

        traces = obj.last_traces(n=3)
        assert len(traces) == 3

    def test_last_traces_empty_when_no_trace_dir(self, tmp_path):
        from diagnostic_base.base import _logger_cache

        class _TraceDev5(DiagnosticBase):
            _log_root = tmp_path

        _logger_cache.pop(_TraceDev5, None)
        obj = _TraceDev5(device_id="td5_nodir")
        assert obj.last_traces() == []

    def test_devices_trace_isolated(self, tmp_path):
        from diagnostic_base.base import _logger_cache

        class _DevA(DiagnosticBase):
            _log_root = tmp_path

        class _DevB(DiagnosticBase):
            _log_root = tmp_path

        _logger_cache.pop(_DevA, None)
        _logger_cache.pop(_DevB, None)
        a = _DevA(device_id="dev_a")
        b = _DevB(device_id="dev_b")
        a.trace_record("from_a")
        b.trace_record("from_b")

        a_traces = a.last_traces()
        b_traces = b.last_traces()
        assert all(t["device"] == "dev_a" for t in a_traces)
        assert all(t["device"] == "dev_b" for t in b_traces)

"""
Tests for BaseShim.dispatch() call-log (T-shim-traffic-spy).

Completion criteria: after a tool call through shim.dispatch(), a JSONL record
appears in the trace directory with device_id, tool_name, latency_ms, success.
(Note: ticket spec said "device" but implementation uses the more precise "device_id".)

Callers must opt in — shim.dispatch("tool", **kwargs) instead of shim.tool(**kwargs).
Migrating specific shim subclasses is future work; this suite proves the mechanism.

All tests redirect via UU_SHIM_TRACE_DIR to avoid polluting the real log dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from unseen_university.shim import BaseShim

# ── Minimal concrete shim ─────────────────────────────────────────────────────


class _StubShim(BaseShim):
    @property
    def device_id(self) -> str:
        return "stub-device"

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        return {"passed": True, "details": "stub"}

    def rollback(self) -> None:
        pass

    def greet(self) -> str:
        return "hello"

    def explode(self) -> None:
        raise RuntimeError("boom")


# ── Fixture: redirect trace to tmp_path ───────────────────────────────────────


@pytest.fixture()
def trace_dir(tmp_path, monkeypatch):
    trace = tmp_path / "trace"
    trace.mkdir()
    monkeypatch.setenv("UU_SHIM_TRACE_DIR", str(trace))
    return trace


def _read_trace_records(trace_dir: Path) -> list[dict]:
    records = []
    for f in sorted(trace_dir.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


# ── Success path ──────────────────────────────────────────────────────────────


class TestDispatchSuccessPath:
    def test_record_written(self, trace_dir):
        shim = _StubShim()
        shim.dispatch("greet")
        records = _read_trace_records(trace_dir)
        assert len(records) == 1

    def test_required_fields_present(self, trace_dir):
        shim = _StubShim()
        shim.dispatch("greet")
        rec = _read_trace_records(trace_dir)[0]
        assert "device_id" in rec
        assert "tool_name" in rec
        assert "latency_ms" in rec
        assert "success" in rec

    def test_device_id_matches_shim(self, trace_dir):
        shim = _StubShim()
        shim.dispatch("greet")
        rec = _read_trace_records(trace_dir)[0]
        assert rec["device_id"] == "stub-device"

    def test_tool_name_recorded(self, trace_dir):
        shim = _StubShim()
        shim.dispatch("greet")
        rec = _read_trace_records(trace_dir)[0]
        assert rec["tool_name"] == "greet"

    def test_success_true(self, trace_dir):
        shim = _StubShim()
        shim.dispatch("greet")
        rec = _read_trace_records(trace_dir)[0]
        assert rec["success"] is True

    def test_error_type_none_on_success(self, trace_dir):
        shim = _StubShim()
        shim.dispatch("greet")
        rec = _read_trace_records(trace_dir)[0]
        assert rec["error_type"] is None

    def test_latency_is_non_negative(self, trace_dir):
        shim = _StubShim()
        shim.dispatch("greet")
        rec = _read_trace_records(trace_dir)[0]
        assert rec["latency_ms"] >= 0

    def test_return_value_propagated(self, trace_dir):
        shim = _StubShim()
        result = shim.dispatch("greet")
        assert result == "hello"

    def test_timestamp_field_present(self, trace_dir):
        shim = _StubShim()
        shim.dispatch("greet")
        rec = _read_trace_records(trace_dir)[0]
        assert "ts" in rec


# ── Tool raises ───────────────────────────────────────────────────────────────


class TestDispatchToolRaises:
    def test_record_written_on_exception(self, trace_dir):
        shim = _StubShim()
        with pytest.raises(RuntimeError):
            shim.dispatch("explode")
        records = _read_trace_records(trace_dir)
        assert len(records) == 1

    def test_success_false_on_exception(self, trace_dir):
        shim = _StubShim()
        with pytest.raises(RuntimeError):
            shim.dispatch("explode")
        rec = _read_trace_records(trace_dir)[0]
        assert rec["success"] is False

    def test_error_type_recorded(self, trace_dir):
        shim = _StubShim()
        with pytest.raises(RuntimeError):
            shim.dispatch("explode")
        rec = _read_trace_records(trace_dir)[0]
        assert rec["error_type"] == "RuntimeError"

    def test_exception_propagates_to_caller(self, trace_dir):
        shim = _StubShim()
        with pytest.raises(RuntimeError, match="boom"):
            shim.dispatch("explode")


# ── Unknown tool (AttributeError) ─────────────────────────────────────────────


class TestDispatchUnknownTool:
    def test_record_written_for_unknown_tool(self, trace_dir):
        shim = _StubShim()
        with pytest.raises(AttributeError):
            shim.dispatch("no_such_tool")
        records = _read_trace_records(trace_dir)
        assert len(records) == 1

    def test_error_type_is_attribute_error(self, trace_dir):
        shim = _StubShim()
        with pytest.raises(AttributeError):
            shim.dispatch("no_such_tool")
        rec = _read_trace_records(trace_dir)[0]
        assert rec["error_type"] == "AttributeError"

    def test_success_false_for_unknown_tool(self, trace_dir):
        shim = _StubShim()
        with pytest.raises(AttributeError):
            shim.dispatch("no_such_tool")
        rec = _read_trace_records(trace_dir)[0]
        assert rec["success"] is False


# ── Multiple dispatches ───────────────────────────────────────────────────────


class TestDispatchMultipleCalls:
    def test_each_call_appends_a_record(self, trace_dir):
        shim = _StubShim()
        shim.dispatch("greet")
        shim.dispatch("greet")
        shim.dispatch("greet")
        records = _read_trace_records(trace_dir)
        assert len(records) == 3

    def test_kwargs_forwarded(self, trace_dir):
        class _KwargsShim(_StubShim):
            def echo(self, msg: str) -> str:
                return msg

        shim = _KwargsShim()
        result = shim.dispatch("echo", msg="ping")
        assert result == "ping"

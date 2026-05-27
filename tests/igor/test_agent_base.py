"""
test_agent_base.py

Tests for AgentBase (devices/igor/tools/agent_base.py) and the
IgorBase thin subclass (devices/igor/igor_base.py).
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── AgentBase ────────────────────────────────────────────────────────────────


class TestAgentBaseNaming:
    def test_get_name_returns_class_and_instance(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        name = obj.get_name()
        assert "AgentBase:" in name

    def test_get_name_uses_subclass_name(self):
        from devices.igor.tools.agent_base import AgentBase

        class MyAgent(AgentBase):
            pass

        my_agent = MyAgent()
        assert "MyAgent:" in my_agent.get_name()

    def test_instance_name_cached(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        name1 = obj.get_name()
        name2 = obj.get_name()
        assert name1 == name2


class TestAgentBaseLogging:
    def test_log_property_returns_logger(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        assert hasattr(obj.log, "debug")
        assert hasattr(obj.log, "info")
        assert hasattr(obj.log, "warning")
        assert hasattr(obj.log, "error")

    def test_log_is_initialized_on_access(self):
        from devices.igor.tools.agent_base import AgentBase

        class FreshAgent(AgentBase):
            _logger = None  # explicit per-class logger slot

        obj = FreshAgent()
        assert FreshAgent._logger is None
        _ = obj.log
        assert FreshAgent._logger is not None


class TestAgentBasePerf:
    def test_time_it_records_perf(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        with obj.time_it("test_op"):
            pass
        assert "test_op" in obj._perf_history
        assert len(obj._perf_history["test_op"]) == 1

    def test_record_perf_caps_at_200(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        for i in range(250):
            obj.record_perf("flood", float(i))
        assert len(obj._perf_history["flood"]) == 200

    def test_perf_summary_format(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        for i in range(10):
            obj.record_perf("test_label", float(i * 10))
        summary = obj._perf_summary("test_label")
        assert "p50=" in summary
        assert "p95=" in summary
        assert "test_label" in summary

    def test_perf_summary_no_data(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        summary = obj._perf_summary()
        assert "no perf data" in summary

    def test_record_perf_writes_to_log_dir(self):
        from devices.igor.tools.agent_base import AgentBase

        with tempfile.TemporaryDirectory() as tmpdir:
            obj = AgentBase(log_dir=Path(tmpdir))
            obj.record_perf("file_test", 42.5)
            log_files = list(Path(tmpdir).glob("perf_*.log"))
            assert len(log_files) == 1
            content = log_files[0].read_text()
            assert "file_test" in content
            assert "42.5ms" in content

    def test_record_perf_no_file_without_log_dir(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()  # no log_dir
        obj.record_perf("mem_only", 10.0)
        # Should not raise, just records in memory
        assert len(obj._perf_history["mem_only"]) == 1


class TestAgentBaseDebug:
    def test_dump(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        obj.custom_attr = "hello"
        d = obj.dump()
        assert "custom_attr" in d
        assert "hello" in d
        assert "AgentBase:" in d

    def test_dump_truncates_long_values(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        obj.big = "x" * 200
        d = obj.dump()
        assert "..." in d

    def test_get_caller(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        caller = obj._get_caller(depth=1)
        assert "test_agent_base.py" in caller


class TestAgentBaseInit:
    def test_zero_args_init(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase()
        assert obj._log_dir is None
        assert isinstance(obj._perf_history, dict)

    def test_custom_log_dir(self):
        from devices.igor.tools.agent_base import AgentBase

        obj = AgentBase(log_dir=Path("/tmp/test_logs"))
        assert obj._log_dir == Path("/tmp/test_logs")

    def test_lazy_perf_history(self):
        """Subclasses that skip super().__init__ still work."""
        from devices.igor.tools.agent_base import AgentBase

        class SkipInit(AgentBase):
            def __init__(self):
                pass  # deliberately skip super().__init__

        obj = SkipInit()
        # _ensure_perf_history should create it lazily
        hist = obj._ensure_perf_history()
        assert isinstance(hist, dict)


# ── IgorBase (thin subclass) ────────────────────────────────────────────────


class TestIgorBaseSubclass:
    def test_igor_base_is_diagnostic_base(self):
        from diagnostic_base.base import DiagnosticBase
        from devices.igor.igor_base import IgorBase

        assert issubclass(IgorBase, DiagnosticBase)

    def test_igor_base_has_log_root(self):
        from devices.igor.igor_base import IgorBase

        obj = IgorBase()
        assert hasattr(obj, "_log_root")
        assert obj._log_root is not None

    def test_igor_base_device_id(self):
        from devices.igor.igor_base import IgorBase

        obj = IgorBase()
        assert obj._device_id == "igor"

    def test_igor_base_log_has_get_timer(self):
        from devices.igor.igor_base import IgorBase

        obj = IgorBase()
        assert hasattr(obj.log, "get_timer")

    def test_igor_base_logger_is_tagged_logger(self):
        from diagnostic_base.tagged_logger import TaggedLogger
        from devices.igor.igor_base import IgorBase

        obj = IgorBase()
        assert isinstance(obj.logger, TaggedLogger)

    def test_igor_base_get_name(self):
        from devices.igor.igor_base import IgorBase

        obj = IgorBase()
        name = obj.get_name()
        assert isinstance(name, str) and len(name) > 0

    def test_igor_base_elapsed_s(self):
        from devices.igor.igor_base import IgorBase

        obj = IgorBase()
        elapsed = obj.elapsed_s()
        assert isinstance(elapsed, float) and elapsed >= 0


class TestIgorBaseBackwardCompat:
    def test_get_logger_importable(self):
        """from ..igor_base import get_logger still works."""
        from devices.igor.igor_base import get_logger

        log = get_logger("test_compat")
        assert hasattr(log, "debug")

    def test_emergency_safe_logger_importable(self):
        from devices.igor.igor_base import _EmergencySafeLogger

        log = _EmergencySafeLogger("test")
        assert hasattr(log, "warning")

    def test_existing_subclass_works(self):
        """A class that inherits IgorBase the old way still works."""
        from devices.igor.igor_base import IgorBase

        class MyComponent(IgorBase):
            def __init__(self):
                super().__init__()
                self.value = 42

        obj = MyComponent()
        assert obj.value == 42
        assert obj._device_id == "igor"
        name = obj.get_name()
        assert isinstance(name, str) and len(name) > 0

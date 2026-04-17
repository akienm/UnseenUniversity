"""
test_rack.py — T-uc-rack-architecture

Tests for the utility closet rack: RackModule, Rack, module lifecycle.
"""

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.utility_closet.rack import ModuleInfo, Rack, RackModule

# ── Test fixtures ────────────────────────────────────────────────────────────


class DummyModule(RackModule):
    """Test module that tracks start/stop calls."""

    def __init__(self, name="dummy", **kwargs):
        super().__init__(name=name, version="1.0.0", module_type="test", **kwargs)
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def health(self):
        return {"online": True, "test_metric": 42}


class SickModule(RackModule):
    """Module that reports unhealthy."""

    def __init__(self):
        super().__init__(name="sick", version="0.1.0", module_type="test")

    def health(self):
        return {"online": False, "reason": "disk full"}


class CrashingModule(RackModule):
    """Module whose health check throws."""

    def __init__(self):
        super().__init__(name="crasher", version="0.1.0", module_type="test")

    def health(self):
        raise RuntimeError("health check exploded")


# ── RackModule ───────────────────────────────────────────────────────────────


class TestRackModule:
    def test_default_health(self):
        mod = RackModule(name="basic", version="1.0.0")
        assert mod.health() == {"online": True}

    def test_info(self):
        mod = RackModule(
            name="mymod",
            version="2.1.0",
            module_type="transport",
            capabilities=["read", "write"],
        )
        info = mod.info()
        assert info.name == "mymod"
        assert info.version == "2.1.0"
        assert info.module_type == "transport"
        assert info.capabilities == ["read", "write"]

    def test_is_agent_base(self):
        from lab.utility_closet.agent_base import AgentBase

        mod = RackModule(name="test")
        assert isinstance(mod, AgentBase)

    def test_start_stop_noop_by_default(self):
        mod = RackModule(name="noop")
        mod.start()  # should not raise
        mod.stop()  # should not raise


# ── Rack registration ────────────────────────────────────────────────────────


class TestRackRegistration:
    def test_register_module(self):
        rack = Rack()
        mod = DummyModule()
        assert rack.register(mod) is True
        assert mod.started is True

    def test_register_duplicate_returns_false(self):
        rack = Rack()
        mod1 = DummyModule(name="dup")
        mod2 = DummyModule(name="dup")
        assert rack.register(mod1) is True
        assert rack.register(mod2) is False

    def test_deregister_module(self):
        rack = Rack()
        mod = DummyModule()
        rack.register(mod)
        assert rack.deregister("dummy") is True
        assert mod.stopped is True

    def test_deregister_nonexistent(self):
        rack = Rack()
        assert rack.deregister("ghost") is False

    def test_get_module(self):
        rack = Rack()
        mod = DummyModule()
        rack.register(mod)
        assert rack.get("dummy") is mod

    def test_get_nonexistent(self):
        rack = Rack()
        assert rack.get("nope") is None

    def test_list_modules(self):
        rack = Rack()
        rack.register(DummyModule(name="a"))
        rack.register(DummyModule(name="b"))
        infos = rack.list_modules()
        names = {i.name for i in infos}
        assert names == {"a", "b"}

    def test_list_empty(self):
        rack = Rack()
        assert rack.list_modules() == []


# ── Rack health ──────────────────────────────────────────────────────────────


class TestRackHealth:
    def test_all_healthy(self):
        rack = Rack()
        rack.register(DummyModule(name="a"))
        rack.register(DummyModule(name="b"))
        h = rack.health()
        assert h["online"] is True
        assert h["module_count"] == 2
        assert h["modules"]["a"]["online"] is True
        assert h["modules"]["b"]["test_metric"] == 42

    def test_one_sick(self):
        rack = Rack()
        rack.register(DummyModule(name="ok"))
        rack.register(SickModule())
        h = rack.health()
        assert h["online"] is False  # one module is down
        assert h["modules"]["ok"]["online"] is True
        assert h["modules"]["sick"]["online"] is False

    def test_crashing_health_check(self):
        rack = Rack()
        rack.register(CrashingModule())
        h = rack.health()
        assert h["online"] is False
        assert h["modules"]["crasher"]["online"] is False
        assert "exploded" in h["modules"]["crasher"]["error"]

    def test_empty_rack_is_online(self):
        rack = Rack()
        h = rack.health()
        assert h["online"] is True
        assert h["module_count"] == 0


# ── Rack lifecycle ───────────────────────────────────────────────────────────


class TestRackLifecycle:
    def test_stop_all(self):
        rack = Rack()
        a = DummyModule(name="a")
        b = DummyModule(name="b")
        rack.register(a)
        rack.register(b)
        rack.stop_all()
        assert a.stopped is True
        assert b.stopped is True
        assert rack.list_modules() == []

    def test_heartbeat(self):
        rack = Rack()
        mod = DummyModule()
        rack.register(mod)
        assert rack.heartbeat("dummy") is True

    def test_heartbeat_unknown(self):
        rack = Rack()
        assert rack.heartbeat("ghost") is False


# ── Thread safety ────────────────────────────────────────────────────────────


class TestRackThreadSafety:
    def test_concurrent_register(self):
        rack = Rack()
        results = []

        def register_mod(i):
            mod = DummyModule(name=f"mod_{i}")
            results.append(rack.register(mod))

        threads = [threading.Thread(target=register_mod, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(results) == 10
        assert len(rack.list_modules()) == 10


# ── Singleton ────────────────────────────────────────────────────────────────


class TestGetRack:
    def test_singleton(self):
        from lab.utility_closet.rack import get_rack

        r1 = get_rack()
        r2 = get_rack()
        assert r1 is r2

    def test_is_rack_instance(self):
        from lab.utility_closet.rack import get_rack

        assert isinstance(get_rack(), Rack)

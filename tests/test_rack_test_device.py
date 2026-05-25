"""
Tests for RackTestDevice and RackTestShim.

Covers:
  - Call recording (every method invocation lands in recorded_calls)
  - Assertion helpers (assert_called, assert_not_called)
  - Failure injection (inject_failure raises on the next call)
  - Injectable health status (healthy / degraded / unhealthy)
  - reset_calls clears the log
  - REAL mode wraps and records through a real BaseDevice
  - Shim lifecycle recording and failure injection
  - Both classes pass the rack contract (BaseDevice / BaseShim)
"""

from __future__ import annotations

import pytest

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.shim import BaseShim
from devices.rack_test.device import RackTestDevice
from devices.rack_test.shim import RackTestShim
from fixtures.stub_devices import StubDevice

# ── Contract ──────────────────────────────────────────────────────────────────


def test_device_is_base_device():
    assert isinstance(RackTestDevice(), BaseDevice)


def test_shim_is_base_shim():
    assert isinstance(RackTestShim(), BaseShim)


def test_interface_version():
    assert RackTestDevice().interface_version() == INTERFACE_VERSION


def test_health_shape():
    h = RackTestDevice().health()
    assert {"status", "detail", "checked_at"} <= h.keys()
    assert h["status"] in ("healthy", "degraded", "unhealthy")


def test_who_am_i_shape():
    w = RackTestDevice().who_am_i()
    assert "device_id" in w
    assert "name" in w
    assert "version" in w


def test_comms_shape():
    c = RackTestDevice().comms()
    assert c["address"].startswith("comms://")
    assert c["mode"] in ("read_only", "write_only", "read_write")


def test_startup_errors_is_list():
    assert isinstance(RackTestDevice().startup_errors(), list)


def test_uptime_is_numeric():
    assert isinstance(RackTestDevice().uptime(), (int, float))


def test_shim_self_test_shape():
    r = RackTestShim().self_test()
    assert isinstance(r["passed"], bool)
    assert "details" in r


def test_shim_device_id():
    assert RackTestShim().device_id == "rack-test"


# ── Call recording ────────────────────────────────────────────────────────────


def test_records_single_call():
    d = RackTestDevice()
    d.health()
    assert len(d.recorded_calls) == 1
    assert d.recorded_calls[0]["method"] == "health"


def test_records_multiple_calls_in_order():
    d = RackTestDevice()
    d.who_am_i()
    d.health()
    d.uptime()
    methods = [c["method"] for c in d.recorded_calls]
    assert methods == ["who_am_i", "health", "uptime"]


def test_result_stored_in_call_record():
    d = RackTestDevice()
    d.health()
    assert d.recorded_calls[0]["result"]["status"] == "healthy"


def test_shim_records_start_stop():
    s = RackTestShim()
    s.start()
    s.stop()
    methods = [c["method"] for c in s.recorded_calls]
    assert methods == ["start", "stop"]


# ── Assertion helpers ─────────────────────────────────────────────────────────


def test_assert_called_passes_after_call():
    d = RackTestDevice()
    d.health()
    d.assert_called("health")  # should not raise


def test_assert_called_fails_when_not_called():
    d = RackTestDevice()
    with pytest.raises(AssertionError, match="health.*never called"):
        d.assert_called("health")


def test_assert_not_called_passes_when_absent():
    d = RackTestDevice()
    d.assert_not_called("health")  # should not raise


def test_assert_not_called_fails_when_present():
    d = RackTestDevice()
    d.health()
    with pytest.raises(AssertionError, match="health.*called unexpectedly"):
        d.assert_not_called("health")


def test_shim_assert_called():
    s = RackTestShim()
    s.start()
    s.assert_called("start")
    s.assert_not_called("stop")


# ── Failure injection ─────────────────────────────────────────────────────────


def test_inject_failure_raises_on_next_call():
    d = RackTestDevice()
    d.inject_failure("health", RuntimeError("simulated failure"))
    with pytest.raises(RuntimeError, match="simulated failure"):
        d.health()


def test_inject_failure_is_one_shot():
    d = RackTestDevice()
    d.inject_failure("health", RuntimeError("one-shot"))
    with pytest.raises(RuntimeError):
        d.health()
    # Second call succeeds
    h = d.health()
    assert h["status"] == "healthy"


def test_inject_failure_only_targets_named_method():
    d = RackTestDevice()
    d.inject_failure("health", RuntimeError("health only"))
    # uptime is not affected
    d.uptime()
    with pytest.raises(RuntimeError):
        d.health()


def test_shim_inject_failure():
    s = RackTestShim()
    s.inject_failure("start", RuntimeError("start blocked"))
    with pytest.raises(RuntimeError, match="start blocked"):
        s.start()
    # Failure recorded
    s.assert_called("start")


# ── Injectable health status ──────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["healthy", "degraded", "unhealthy"])
def test_health_status_injectable(status):
    d = RackTestDevice(health_status=status)
    assert d.health()["status"] == status


# ── reset_calls ───────────────────────────────────────────────────────────────


def test_reset_calls_clears_log():
    d = RackTestDevice()
    d.health()
    d.who_am_i()
    d.reset_calls()
    assert d.recorded_calls == []


def test_shim_reset_calls():
    s = RackTestShim()
    s.start()
    s.reset_calls()
    assert s.recorded_calls == []


# ── REAL mode ─────────────────────────────────────────────────────────────────


def test_real_mode_requires_real_device():
    with pytest.raises(ValueError, match="real_device"):
        RackTestDevice(mode=RackTestDevice.MODE_REAL)


def test_real_mode_delegates_and_records():
    stub = StubDevice()
    d = RackTestDevice(mode=RackTestDevice.MODE_REAL, real_device=stub)
    result = d.health()
    assert result["status"] == "healthy"
    d.assert_called("health")


def test_real_mode_records_who_am_i_result():
    stub = StubDevice()
    d = RackTestDevice(mode=RackTestDevice.MODE_REAL, real_device=stub)
    w = d.who_am_i()
    assert w["device_id"] == "stub"
    assert d.recorded_calls[0]["result"]["device_id"] == "stub"


def test_real_mode_failure_injection_intercepts_before_delegate():
    stub = StubDevice()
    d = RackTestDevice(mode=RackTestDevice.MODE_REAL, real_device=stub)
    d.inject_failure("health", RuntimeError("injected before real device"))
    with pytest.raises(RuntimeError, match="injected before real device"):
        d.health()


# ── Lifecycle state (simulated) ───────────────────────────────────────────────


def test_block_sets_state():
    d = RackTestDevice()
    d.block("test reason")
    assert d._blocked is True
    assert d._block_reason == "test reason"
    d.assert_called("block")


def test_halt_sets_blocked():
    d = RackTestDevice()
    d.halt()
    assert d._blocked is True
    d.assert_called("halt")


def test_restart_clears_block():
    d = RackTestDevice()
    d.block("blocked")
    d.restart()
    assert d._blocked is False


def test_recovery_clears_block():
    d = RackTestDevice()
    d.halt()
    d.recovery()
    assert d._blocked is False

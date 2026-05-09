"""
Contract test suite — every BaseDevice implementation passes the rack contract.

Add new device classes to ALL_DEVICE_CLASSES as each phase ships.
A class that misses any abstract method fails at import time (TypeError),
so this parametrized suite is the second line of defence: it verifies
the return *shapes* are correct.
"""

import pytest

from agent_datacenter.device import BaseDevice, INTERFACE_VERSION
from agent_datacenter.shim import BaseShim
from devices.browser_use.device import BrowserUseDevice
from devices.browser_use.shim import BrowserUseShim
from devices.claude.device import ClaudeDevice
from devices.claude.shim import ClaudeShim
from devices.discord_bot.device import DiscordBotDevice
from devices.discord_bot.shim import DiscordBotShim
from devices.igor.device import IgorDevice
from devices.igor.shim import IgorShim
from devices.inference.device import InferenceDevice
from devices.inference.shim import InferenceShim
from devices.postgres.device import PostgresDevice
from devices.rack_test.device import RackTestDevice
from devices.rack_test.shim import RackTestShim
from devices.sensor.device import SensorDevice
from devices.sensor.shim import SensorShim
from devices.swadl.device import SwadlDevice
from devices.swadl.shim import SwadlShim
from devices.template.device import TemplateDevice
from devices.template.shim import TemplateShim
from tests.fixtures.stub_devices import StubDevice, StubShim

# Extend this list as each device phase ships
ALL_DEVICE_CLASSES = [
    PostgresDevice,
    TemplateDevice,
    StubDevice,
    BrowserUseDevice,
    IgorDevice,
    InferenceDevice,
    ClaudeDevice,
    DiscordBotDevice,
    SwadlDevice,
    RackTestDevice,
    SensorDevice,
]

ALL_SHIM_CLASSES = [
    TemplateShim,
    StubShim,
    BrowserUseShim,
    IgorShim,
    InferenceShim,
    ClaudeShim,
    DiscordBotShim,
    SwadlShim,
    RackTestShim,
    SensorShim,
]


@pytest.mark.parametrize("device_class", ALL_DEVICE_CLASSES)
def test_implements_full_contract(device_class):
    """Device must be a concrete BaseDevice subclass (no missing abstract methods)."""
    d = device_class()
    assert isinstance(d, BaseDevice)


@pytest.mark.parametrize("device_class", ALL_DEVICE_CLASSES)
def test_interface_version(device_class):
    d = device_class()
    assert d.interface_version() == INTERFACE_VERSION


@pytest.mark.parametrize("device_class", ALL_DEVICE_CLASSES)
def test_health_shape(device_class):
    d = device_class()
    h = d.health()
    assert isinstance(h, dict)
    assert "status" in h
    assert h["status"] in ("healthy", "degraded", "unhealthy")
    assert "checked_at" in h


@pytest.mark.parametrize("device_class", ALL_DEVICE_CLASSES)
def test_who_am_i_shape(device_class):
    d = device_class()
    w = d.who_am_i()
    assert isinstance(w, dict)
    assert "device_id" in w
    assert "name" in w
    assert "version" in w


@pytest.mark.parametrize("device_class", ALL_DEVICE_CLASSES)
def test_comms_shape(device_class):
    d = device_class()
    c = d.comms()
    assert isinstance(c, dict)
    assert "address" in c
    assert c["address"].startswith("comms://")
    assert "mode" in c
    assert c["mode"] in ("read_only", "write_only", "read_write")


@pytest.mark.parametrize("device_class", ALL_DEVICE_CLASSES)
def test_startup_errors_is_list(device_class):
    d = device_class()
    assert isinstance(d.startup_errors(), list)


@pytest.mark.parametrize("device_class", ALL_DEVICE_CLASSES)
def test_uptime_is_numeric(device_class):
    d = device_class()
    assert isinstance(d.uptime(), (int, float))


@pytest.mark.parametrize("shim_class", ALL_SHIM_CLASSES)
def test_shim_implements_contract(shim_class):
    s = shim_class()
    assert isinstance(s, BaseShim)
    assert isinstance(s.device_id, str)


@pytest.mark.parametrize("shim_class", ALL_SHIM_CLASSES)
def test_shim_self_test_shape(shim_class):
    s = shim_class()
    result = s.self_test()
    assert isinstance(result, dict)
    assert "passed" in result
    assert isinstance(result["passed"], bool)
    assert "details" in result


def test_stub_device_instantiates():
    d = StubDevice()
    assert d.who_am_i()["device_id"] == "stub"
    assert d.health()["status"] == "healthy"


def test_abstract_device_not_instantiable():
    """Confirm the ABC enforcement works."""

    class Incomplete(BaseDevice):
        pass

    with pytest.raises(TypeError):
        Incomplete()


def test_abstract_shim_not_instantiable():
    class Incomplete(BaseShim):
        pass

    with pytest.raises(TypeError):
        Incomplete()

"""
Contract test suite — every BaseDevice implementation passes the rack contract.

Add new device classes to ALL_DEVICE_CLASSES as each phase ships.
A class that misses any abstract method fails at import time (TypeError),
so this parametrized suite is the second line of defence: it verifies
the return *shapes* are correct.
"""

import pytest

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.shim import BaseShim
from unseen_university.devices.auditor.device import AuditorDevice
from unseen_university.devices.auditor.shim import AuditorShim
from unseen_university.devices.browser_use.device import BrowserUseDevice
from unseen_university.devices.browser_use.shim import BrowserUseShim
from unseen_university.devices.evaluator.device import EvaluatorDevice
from unseen_university.devices.evaluator.shim import EvaluatorShim
from unseen_university.devices.claude.device import ClaudeDevice
from unseen_university.devices.claude.shim import ClaudeShim
from unseen_university.devices.discord_bot.device import DiscordBotDevice
from unseen_university.devices.discord_bot.shim import DiscordBotShim
from unseen_university.devices.granny.device import GrannyWeatherwaxDevice
from unseen_university.devices.granny.shim import GrannyShim
from unseen_university.devices.igor.device import IgorDevice
from unseen_university.devices.igor.shim import IgorShim
from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.shim import InferenceShim
from unseen_university.devices.minion.device import MinionDevice
from unseen_university.devices.minion.shim import MinionShim
from unseen_university.devices.nanny.device import NannyOggDevice
from unseen_university.devices.postgres.device import PostgresDevice
from unseen_university.devices.queue.device import QueueDevice
from unseen_university.devices.rack_test.device import RackTestDevice
from unseen_university.devices.rack_test.shim import RackTestShim
from unseen_university.devices.reader.device import ReaderDevice
from unseen_university.devices.scraps.scraps_device import ScrapsDevice
from unseen_university.devices.scraps.shim import ScrapsShim
from unseen_university.devices.sensor.device import SensorDevice
from unseen_university.devices.sensor.shim import SensorShim
from unseen_university.devices.summarizer.device import SummarizerDevice
from unseen_university.devices.summarizer.shim import SummarizerShim
from unseen_university.devices.swadl.device import SwadlDevice
from unseen_university.devices.swadl.shim import SwadlShim
from unseen_university.devices.template.device import TemplateDevice
from unseen_university.devices.template.shim import TemplateShim
from unseen_university.devices.web_server.device import WebServerDevice
from unseen_university.devices.web_server.shim import WebServerShim
from unseen_university.devices.workspace.device import WorkspaceDevice
from unseen_university.devices.workspace.shim import WorkspaceShim
from fixtures.stub_devices import StubDevice, StubShim

# Extend this list as each device phase ships
ALL_DEVICE_CLASSES = [
    AuditorDevice,
    BrowserUseDevice,
    ClaudeDevice,
    DiscordBotDevice,
    EvaluatorDevice,
    GrannyWeatherwaxDevice,
    IgorDevice,
    InferenceDevice,
    MinionDevice,
    NannyOggDevice,
    PostgresDevice,
    QueueDevice,
    RackTestDevice,
    ReaderDevice,
    ScrapsDevice,
    SensorDevice,
    StubDevice,
    SummarizerDevice,
    SwadlDevice,
    TemplateDevice,
    WebServerDevice,
    WorkspaceDevice,
]

ALL_SHIM_CLASSES = [
    AuditorShim,
    BrowserUseShim,
    ClaudeShim,
    DiscordBotShim,
    EvaluatorShim,
    GrannyShim,
    IgorShim,
    InferenceShim,
    MinionShim,
    RackTestShim,
    ScrapsShim,
    SensorShim,
    StubShim,
    SummarizerShim,
    SwadlShim,
    TemplateShim,
    WebServerShim,
    WorkspaceShim,
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

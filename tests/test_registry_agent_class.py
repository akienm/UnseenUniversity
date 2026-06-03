"""Tests for agent_class field on device registrations and datacenter_manifest exposure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.device_config import DeviceConfig
from devices.igor.device import IgorDevice
from devices.minion.device import MinionDevice
from devices.nanny.device import NannyOggDevice
from devices.scraps.scraps_device import ScrapsDevice
from skeleton.registry import DeviceRegistry
from unseen_university.devices.librarian.tools.manifest_tools import datacenter_manifest

# ── Registry: agent_class stored and retrieved ────────────────────────────────


def test_register_stores_agent_class(tmp_path: Path) -> None:
    reg = DeviceRegistry(path=tmp_path / "devices.json")
    reg.register(
        "igor-wild-0001",
        DeviceConfig(),
        "comms://igor-wild-0001",
        agent_class="general",
    )
    record = reg.get_device("igor-wild-0001")
    assert record["agent_class"] == "general"


def test_register_default_agent_class_is_utility(tmp_path: Path) -> None:
    reg = DeviceRegistry(path=tmp_path / "devices.json")
    reg.register("mydevice", DeviceConfig(), "comms://mydevice")
    record = reg.get_device("mydevice")
    assert record["agent_class"] == "utility"


def test_list_devices_includes_agent_class(tmp_path: Path) -> None:
    reg = DeviceRegistry(path=tmp_path / "devices.json")
    reg.register("granny", DeviceConfig(), "comms://granny", agent_class="specialized")
    reg.register("minion", DeviceConfig(), "comms://minion", agent_class="utility")
    devices = {d["id"]: d for d in reg.list_devices()}
    assert devices["granny"]["agent_class"] == "specialized"
    assert devices["minion"]["agent_class"] == "utility"


# ── who_am_i() declarations ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "device_cls, expected_class",
    [
        (IgorDevice, "general"),
        (NannyOggDevice, "specialized"),
        (MinionDevice, "utility"),
        (ScrapsDevice, "utility"),
    ],
)
def test_who_am_i_declares_agent_class(device_cls, expected_class) -> None:
    identity = device_cls.AGENT_CLASS
    assert (
        identity == expected_class
    ), f"{device_cls.__name__}.AGENT_CLASS should be {expected_class!r}, got {identity!r}"


# ── datacenter_manifest exposes agent_class per device ───────────────────────


def test_datacenter_manifest_devices_section(tmp_path: Path) -> None:
    reg_path = tmp_path / "devices.json"
    reg = DeviceRegistry(path=reg_path)
    reg.register(
        "igor-wild-0001",
        DeviceConfig(),
        "comms://igor-wild-0001",
        agent_class="general",
    )
    reg.register("granny", DeviceConfig(), "comms://granny", agent_class="specialized")

    result = json.loads(
        datacenter_manifest(routing_only=False, _registry_path=reg_path)
    )

    assert "devices" in result
    by_id = {d["device_id"]: d for d in result["devices"]}

    # Registry-sourced devices
    assert by_id["igor-wild-0001"]["agent_class"] == "general"
    assert by_id["granny"]["agent_class"] == "specialized"

    # Librarian always present (self-declared)
    assert "librarian" in by_id
    assert by_id["librarian"]["agent_class"] == "utility"


def test_datacenter_manifest_routing_only_omits_devices(tmp_path: Path) -> None:
    reg_path = tmp_path / "devices.json"
    reg = DeviceRegistry(path=reg_path)
    reg.register(
        "igor-wild-0001",
        DeviceConfig(),
        "comms://igor-wild-0001",
        agent_class="general",
    )

    result = json.loads(datacenter_manifest(routing_only=True, _registry_path=reg_path))
    assert "devices" not in result


def test_datacenter_manifest_graceful_when_registry_missing(tmp_path: Path) -> None:
    absent = tmp_path / "nonexistent.json"
    result = json.loads(datacenter_manifest(routing_only=False, _registry_path=absent))
    assert "devices" in result
    # Librarian self-declaration should still be present
    by_id = {d["device_id"]: d for d in result["devices"]}
    assert "librarian" in by_id
    assert by_id["librarian"]["agent_class"] == "utility"


def test_agent_class_values_are_valid() -> None:
    valid = {"utility", "specialized", "general"}
    reg_path = Path("/nonexistent/path.json")
    result = json.loads(
        datacenter_manifest(routing_only=False, _registry_path=reg_path)
    )
    for device in result["devices"]:
        assert (
            device["agent_class"] in valid
        ), f"device {device['device_id']!r} has invalid agent_class {device['agent_class']!r}"

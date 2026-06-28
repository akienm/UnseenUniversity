"""
BaseDevice + BaseShim contract tests.

Parameterized over every non-Igor device that can be constructed in-process
without live infra. Each test verifies that the required keys and return
shapes are present — certifying that new devices conform to the rack contract.

BaseDevice required shapes (from unseen_university/device.py docstrings):
  who_am_i     → {device_id, name, version}
  requirements → {deps: list}
  capabilities → {can_send, can_receive, emitted_keywords: list}
  comms        → {address (comms://...), mode, supports_push, supports_pull, supports_nudge}
  interface_version → INTERFACE_VERSION string
  health       → {status ∈ {healthy,degraded,unhealthy}, detail, checked_at}
  uptime       → float ≥ 0
  startup_errors → list
  logs         → {paths: dict}
  update_info  → {current_version, update_available}
  where_and_how → {host, pid, launch_command}
  restart / block / halt / recovery → no raise
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.device import INTERFACE_VERSION, BaseDevice

# ── Device factory ────────────────────────────────────────────────────────────

_DEVICE_IDS = [
    "rack_test",
    "sensor",
    "workspace",
    "scraps",
    "granny",
    "postgres",
    "queue",
    "web_server",
    "inference",
    "minion",
    "archivist",
]


@pytest.fixture(params=_DEVICE_IDS)
def contract_device(request, tmp_path):
    name = request.param

    if name == "rack_test":
        from unseen_university.devices.rack_test.device import RackTestDevice

        return RackTestDevice()

    if name == "sensor":
        from unseen_university.devices.sensor.device import SensorDevice

        return SensorDevice()

    if name == "workspace":
        from unseen_university.devices.workspace.device import WorkspaceDevice

        return WorkspaceDevice(workspace_root=tmp_path)

    if name == "scraps":
        from unseen_university.devices.scraps.scraps_device import ScrapsDevice

        return ScrapsDevice()

    if name == "granny":
        # Granny is now a rules-engine daemon; no standalone Device class
        pytest.skip("granny runs as daemon process, not a BaseDevice subclass")

    if name == "postgres":
        from unseen_university.devices.postgres.device import PostgresDevice

        return PostgresDevice()

    if name == "queue":
        import json

        import unseen_university.devices.queue.device as qdev
        from unseen_university.devices.queue.device import QueueDevice

        gate = tmp_path / "gate.json"
        gate.write_text(json.dumps({"tripped": False}))
        orig_gate = qdev.GATE_FILE
        qdev.GATE_FILE = gate
        with patch("unseen_university.devices.queue.device._db_conn") as mc:
            mc.return_value.close = MagicMock()
            dev = QueueDevice()
        qdev.GATE_FILE = orig_gate
        return dev

    if name == "web_server":
        from unseen_university.devices.web_server.device import WebServerDevice

        return WebServerDevice()

    if name == "inference":
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.sources import SourceRegistry
        from unseen_university.devices.inference.models_registry import default_registry

        sources = SourceRegistry()
        dev = InferenceDevice(sources=sources, models=default_registry())
        request.addfinalizer(dev._health.stop)
        return dev

    if name == "minion":
        from unseen_university.devices.minion.device import MinionDevice

        inf = MagicMock()
        inf.health.return_value = {"status": "healthy", "detail": "mock"}
        inf.startup_errors.return_value = []
        return MinionDevice(inference=inf)

    if name == "archivist":
        from unseen_university.devices.archivist.device import ArchivistDevice
        from unseen_university.devices.inference.shim import InferenceResponse

        inf = MagicMock()
        inf.health.return_value = {"status": "healthy", "detail": "mock"}
        inf.startup_errors.return_value = []
        inf.dispatch.return_value = InferenceResponse(text="", model="")
        return ArchivistDevice(inference=inf)

    raise ValueError(f"unknown device id: {name!r}")


# ── Required-keys contract ────────────────────────────────────────────────────


def test_who_am_i_has_device_id(contract_device):
    info = contract_device.who_am_i()
    assert (
        "device_id" in info
    ), f"who_am_i missing 'device_id' in {type(contract_device).__name__}"
    assert isinstance(info["device_id"], str)
    assert info["device_id"]


def test_who_am_i_has_name(contract_device):
    info = contract_device.who_am_i()
    assert "name" in info


def test_who_am_i_has_version(contract_device):
    info = contract_device.who_am_i()
    assert "version" in info


def test_requirements_has_deps_list(contract_device):
    reqs = contract_device.requirements()
    assert (
        "deps" in reqs
    ), f"requirements missing 'deps' in {type(contract_device).__name__}"
    assert isinstance(reqs["deps"], list)


def test_capabilities_has_can_send(contract_device):
    caps = contract_device.capabilities()
    assert "can_send" in caps


def test_capabilities_has_can_receive(contract_device):
    caps = contract_device.capabilities()
    assert "can_receive" in caps


def test_capabilities_has_emitted_keywords(contract_device):
    caps = contract_device.capabilities()
    assert "emitted_keywords" in caps
    assert isinstance(caps["emitted_keywords"], list)


def test_comms_has_address(contract_device):
    c = contract_device.comms()
    assert "address" in c
    assert c["address"].startswith("comms://"), (
        f"comms.address must start with comms://, got {c['address']!r} "
        f"in {type(contract_device).__name__}"
    )


def test_comms_has_mode(contract_device):
    c = contract_device.comms()
    assert "mode" in c
    assert c["mode"] in ("read_only", "write_only", "read_write")


def test_comms_has_push_pull_nudge_flags(contract_device):
    c = contract_device.comms()
    for key in ("supports_push", "supports_pull", "supports_nudge"):
        assert key in c, f"comms missing {key!r} in {type(contract_device).__name__}"
        assert isinstance(c[key], bool)


def test_interface_version_matches_constant(contract_device):
    assert contract_device.interface_version() == INTERFACE_VERSION


def test_health_has_status(contract_device):
    with (
        patch("unseen_university.devices.postgres.device._pg_connect", return_value=None),
        patch("unseen_university.devices.inference.device._openrouter_reachable", return_value=False),
        patch("unseen_university.devices.web_server.device._check_health", return_value=None),
    ):
        h = contract_device.health()
    assert "status" in h
    assert h["status"] in ("healthy", "degraded", "unhealthy"), (
        f"health.status must be one of healthy/degraded/unhealthy, "
        f"got {h['status']!r} in {type(contract_device).__name__}"
    )


def test_health_has_detail_and_checked_at(contract_device):
    with (
        patch("unseen_university.devices.postgres.device._pg_connect", return_value=None),
        patch("unseen_university.devices.inference.device._openrouter_reachable", return_value=False),
        patch("unseen_university.devices.web_server.device._check_health", return_value=None),
    ):
        h = contract_device.health()
    assert "detail" in h
    assert "checked_at" in h


def test_uptime_is_non_negative_float(contract_device):
    time.sleep(0.01)
    u = contract_device.uptime()
    assert isinstance(u, (int, float))
    assert u >= 0


def test_startup_errors_is_list(contract_device):
    errs = contract_device.startup_errors()
    assert isinstance(errs, list)


def test_logs_has_paths(contract_device):
    logs = contract_device.logs()
    assert "paths" in logs
    assert isinstance(logs["paths"], dict)


def test_update_info_has_current_version(contract_device):
    info = contract_device.update_info()
    assert "current_version" in info


def test_update_info_has_update_available(contract_device):
    info = contract_device.update_info()
    assert "update_available" in info
    assert isinstance(info["update_available"], bool)


def test_where_and_how_has_host(contract_device):
    w = contract_device.where_and_how()
    assert (
        "host" in w
    ), f"where_and_how missing 'host' in {type(contract_device).__name__}"


def test_where_and_how_has_pid(contract_device):
    w = contract_device.where_and_how()
    assert (
        "pid" in w
    ), f"where_and_how missing 'pid' in {type(contract_device).__name__}"
    assert isinstance(w["pid"], (int, type(None)))


def test_where_and_how_has_launch_command(contract_device):
    w = contract_device.where_and_how()
    assert (
        "launch_command" in w
    ), f"where_and_how missing 'launch_command' in {type(contract_device).__name__}"


# ── Lifecycle methods don't raise ────────────────────────────────────────────


def test_restart_does_not_raise(contract_device):
    contract_device.restart()


def test_block_does_not_raise(contract_device):
    contract_device.block("contract-test-block")


def test_halt_does_not_raise(contract_device):
    contract_device.halt()


def test_recovery_does_not_raise(contract_device):
    contract_device.recovery()


# ── is_a BaseDevice ───────────────────────────────────────────────────────────


def test_is_base_device_subclass(contract_device):
    assert isinstance(contract_device, BaseDevice)

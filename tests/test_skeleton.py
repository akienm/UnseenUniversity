"""
Skeleton tests — registration, health rollup, namespace collision.

Most tests use StubDevice and an in-memory registry (tmp_path) — no real
IMAP required. Announce-bootstrap tests opt into the IMAP stub via
AGENT_DATACENTER_TEST_MODE=1 and exercise comms://announce mailboxes.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

# Set test mode BEFORE importing anything that pulls in bus.imap_server.
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.devices.skeleton.exceptions import AuthError, RegistrationError
from unseen_university.devices.skeleton.health import rack_health_async
from unseen_university.devices.skeleton.skeleton import Skeleton
from unseen_university.config.device_config import DeviceConfig
from unseen_university.devices.skeleton.registry import DeviceRegistry
from fixtures.stub_devices import StubDevice

CANONICAL_PROFILES = Path(__file__).parent.parent / "unseen_university" / "config" / "profiles"


def _make_skeleton(tmp_path: Path) -> Skeleton:
    registry = DeviceRegistry(path=tmp_path / "devices.json")
    return Skeleton(registry=registry)


def _make_skeleton_with_bus(tmp_path: Path):
    """Returns (skeleton, imap_server, profiles_dir). Caller must server.stop()."""
    from unseen_university.devices.bus.imap_server import IMAPServer

    registry = DeviceRegistry(path=tmp_path / "devices.json")
    server = IMAPServer()
    server.start()
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", profiles_dir / "igor.yaml")
    skel = Skeleton(registry=registry, imap_server=server, profiles_dir=profiles_dir)
    return skel, server, profiles_dir


# ── Registration ──────────────────────────────────────────────────────────────


def test_skeleton_self_registers(tmp_path):
    skel = _make_skeleton(tmp_path)
    ids = [d["id"] for d in skel._registry.list_devices()]
    assert "skeleton" in ids


def test_register_device_appears_in_registry(tmp_path):
    skel = _make_skeleton(tmp_path)
    device = StubDevice()
    skel.register_device(device)
    ids = [d["id"] for d in skel._registry.list_devices()]
    assert "stub" in ids


def test_register_device_status_online(tmp_path):
    skel = _make_skeleton(tmp_path)
    skel.register_device(StubDevice())
    record = skel._registry.get_device("stub")
    assert record is not None
    assert record["status"] == "online"


def test_deregister_device_status_offline(tmp_path):
    skel = _make_skeleton(tmp_path)
    skel.register_device(StubDevice())
    skel.deregister_device("stub")
    record = skel._registry.get_device("stub")
    # Device record persists after deregistration (mailbox retention)
    assert record is not None
    assert record["status"] == "offline"
    # Not in live devices dict
    assert "stub" not in skel._devices


def test_deregister_allows_reattach(tmp_path):
    skel = _make_skeleton(tmp_path)
    skel.register_device(StubDevice())
    skel.deregister_device("stub")
    # Re-registration after offline must succeed (reattach scenario)
    skel.register_device(StubDevice())
    assert "stub" in skel._devices


# ── Collision ─────────────────────────────────────────────────────────────────


def test_namespace_collision_raises(tmp_path):
    skel = _make_skeleton(tmp_path)
    skel.register_device(StubDevice())
    with pytest.raises(RegistrationError, match="already registered"):
        skel.register_device(StubDevice())


def test_collision_after_reregister_not_deregistered(tmp_path):
    """Online device cannot be reregistered without deregister first."""
    skel = _make_skeleton(tmp_path)
    skel.register_device(StubDevice())
    # Device is now online; second call must fail even with same object
    with pytest.raises(RegistrationError):
        skel.register_device(StubDevice())
    # Registry state unchanged (no duplicate entry)
    stubs = [d for d in skel._registry.list_devices() if d["id"] == "stub"]
    assert len(stubs) == 1


# ── Health rollup ─────────────────────────────────────────────────────────────


def test_health_rollup_healthy(tmp_path):
    skel = _make_skeleton(tmp_path)
    skel.register_device(StubDevice())
    result = asyncio.run(rack_health_async(skel._devices))
    assert "stub" in result
    assert result["stub"]["healthy"] is True
    assert result["stub"]["error"] is None


def test_health_rollup_empty_when_no_devices(tmp_path):
    skel = _make_skeleton(tmp_path)
    result = asyncio.run(rack_health_async(skel._devices))
    # Only skeleton is in registry, but it's not in _devices (Skeleton manages itself)
    assert result == {}


def test_health_rollup_captures_exception(tmp_path):
    class BrokenDevice(StubDevice):
        def health(self):
            raise RuntimeError("disk gone")

    skel = _make_skeleton(tmp_path)
    # Manually inject a broken device — bypass register_device id collision check
    skel._devices["broken"] = BrokenDevice()
    result = asyncio.run(rack_health_async(skel._devices))
    assert "broken" in result
    assert result["broken"]["healthy"] is False
    assert "disk gone" in result["broken"]["error"]


def test_health_rollup_timeout(tmp_path):
    class SlowDevice(StubDevice):
        def health(self):
            time.sleep(5)
            return {"status": "healthy", "checked_at": "x"}

    skel = _make_skeleton(tmp_path)
    skel._devices["slow"] = SlowDevice()
    result = asyncio.run(rack_health_async(skel._devices, timeout=0.1))
    assert result["slow"]["healthy"] is False
    assert result["slow"]["error"] == "timeout"


# ── Access control ────────────────────────────────────────────────────────────


def _make_skeleton_with_token(tmp_path: Path, caller: str) -> tuple[Skeleton, str]:
    """Return (skel, proof_token) — token is issued for caller."""
    from unseen_university.announce.provenance import ProvenanceService

    prov = ProvenanceService(
        registry_dir=tmp_path / "prov_registry",
        secret_path=tmp_path / "rack.secret",
    )
    registry = DeviceRegistry(path=tmp_path / "devices.json")
    skel = Skeleton(registry=registry, provenance=prov)
    token = prov.issue_token(caller, "test-session", 0.0)
    return skel, token


def test_auth_halt_skeleton_caller_allowed(tmp_path):
    skel, token = _make_skeleton_with_token(tmp_path, "skeleton")
    skel.register_device(StubDevice())
    skel._check_caller_auth("skeleton", "stub", "halt", token)  # must not raise


def test_auth_halt_self_allowed(tmp_path):
    skel, token = _make_skeleton_with_token(tmp_path, "stub")
    skel.register_device(StubDevice())
    skel._check_caller_auth("stub", "stub", "halt", token)  # must not raise


def test_auth_halt_third_party_raises(tmp_path):
    skel, token = _make_skeleton_with_token(tmp_path, "CC.0")
    skel.register_device(StubDevice())
    with pytest.raises(AuthError):
        skel._check_caller_auth("CC.0", "stub", "halt", token)


def test_auth_halt_missing_proof_raises(tmp_path):
    """Correct from_device but missing proof token — must still be denied."""
    skel, _ = _make_skeleton_with_token(tmp_path, "skeleton")
    skel.register_device(StubDevice())
    with pytest.raises(AuthError, match="proof token"):
        skel._check_caller_auth("skeleton", "stub", "halt", "")


# ── Announce bootstrap (slice 2) ──────────────────────────────────────────────


def test_announce_bootstrap_creates_mailboxes(tmp_path):
    skel, server, _ = _make_skeleton_with_bus(tmp_path)
    try:
        mailboxes = server.list_mailboxes()
        assert "announce" in mailboxes
        assert "announce-events" in mailboxes
    finally:
        server.stop()


def test_announce_bootstrap_wires_broker_and_listener(tmp_path):
    skel, server, _ = _make_skeleton_with_bus(tmp_path)
    try:
        assert skel._announce_broker is not None
        assert skel._announce_listener is not None
    finally:
        server.stop()


def test_announce_pump_is_noop_without_envelopes(tmp_path):
    skel, server, _ = _make_skeleton_with_bus(tmp_path)
    try:
        assert skel.announce_pump() == 0
    finally:
        server.stop()


def test_skeleton_without_bus_skips_announce(tmp_path):
    """Skeleton with imap_server=None must not create broker/listener."""
    skel = _make_skeleton(tmp_path)
    assert skel._announce_broker is None
    assert skel._announce_listener is None
    assert skel.announce_pump() == 0

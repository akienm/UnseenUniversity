"""
IgorShim tests — install/connect lifecycle + capability-reading forwarders.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

# Test mode must be set BEFORE bus.imap_server is imported.
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

import pytest

from unseen_university.announce import (
    ANNOUNCE_EVENTS_MAILBOX,
    ANNOUNCE_MAILBOX,
    AnnounceBroker,
    AnnounceListener,
    IgorShim,
)
from bus.imap_server import IMAPServer

CANONICAL_PROFILES = Path(__file__).parent.parent / "config" / "profiles"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def server():
    s = IMAPServer()
    s.start()
    s.create_mailbox(ANNOUNCE_MAILBOX)
    s.create_mailbox(ANNOUNCE_EVENTS_MAILBOX)
    yield s
    s.stop()


class _FakeRegistry:
    def list_devices(self):
        return [
            {"device_id": "inference", "status": "online"},
            {"device_id": "postgres", "status": "online"},
        ]


@pytest.fixture()
def listener(server, tmp_path):
    profiles = tmp_path / "broker_profiles"
    profiles.mkdir()
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", profiles / "igor.yaml")
    broker = AnnounceBroker(profiles_dir=profiles, registry=_FakeRegistry(), devices={})
    return AnnounceListener(broker=broker, imap_server=server, from_device="skeleton")


def _drive_pump_in_background(listener, stop):
    def _run():
        while not stop.is_set():
            listener.pump()
            time.sleep(0.02)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ── install() ─────────────────────────────────────────────────────────────────


def test_install_creates_runtime_profile(server, tmp_path):
    runtime_profiles = tmp_path / "runtime"
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=runtime_profiles,
    )
    assert not (runtime_profiles / "igor.yaml").exists()
    shim.install()
    assert (runtime_profiles / "igor.yaml").exists()


def test_install_idempotent(server, tmp_path):
    runtime_profiles = tmp_path / "runtime"
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=runtime_profiles,
    )
    shim.install()
    # Sentinel: write a marker into the file. Second install() must NOT overwrite.
    runtime_yaml = runtime_profiles / "igor.yaml"
    runtime_yaml.write_text(runtime_yaml.read_text() + "\n# user-edit marker\n")
    shim.install()
    assert "user-edit marker" in runtime_yaml.read_text()


def test_install_raises_when_canonical_missing(server, tmp_path):
    runtime_profiles = tmp_path / "runtime"
    bogus_canonical = tmp_path / "no-such-canonical"
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=runtime_profiles,
        canonical_profiles_dir=bogus_canonical,
    )
    with pytest.raises(FileNotFoundError):
        shim.install()


# ── connect() ─────────────────────────────────────────────────────────────────


def test_connect_announces_and_caches_client(server, listener, tmp_path):
    runtime_profiles = tmp_path / "runtime"
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=runtime_profiles,
        box="testhost",
        box_n=0,
    )
    # Broker resolves against the broker's profiles dir (separate from runtime
    # in this test). The shim's runtime dir is for documentation/install only.
    stop = threading.Event()
    pumper = _drive_pump_in_background(listener, stop)
    try:
        manifest = shim.connect(timeout=2.0, proof={"shared_secret": "test-rack-secret"})
    finally:
        stop.set()
        pumper.join(timeout=1.0)

    assert manifest["issued_to"]["agent_id"] == "igor-wild-test"
    assert shim.client is not None
    assert shim.manifest is manifest


def test_connect_raises_connection_error_on_timeout(server, tmp_path):
    """No listener running → announce times out → ConnectionError."""
    runtime_profiles = tmp_path / "runtime"
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=runtime_profiles,
    )
    with pytest.raises(ConnectionError, match="timed out"):
        shim.connect(timeout=0.2)


# ── capability-reading forwarders ─────────────────────────────────────────────


def test_capability_accessors_empty_before_connect(server, tmp_path):
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=tmp_path / "runtime",
    )
    assert shim.client is None
    assert shim.manifest is None
    assert shim.get_tools() == []
    assert shim.get_state_refs() == []
    assert shim.get_channels() == []
    assert shim.get_tool("inference") is None
    assert shim.get_state_ref("twm") is None
    assert shim.get_primary_address() is None


def test_capability_accessors_forward_after_connect(server, listener, tmp_path):
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=tmp_path / "runtime",
        box="testhost",
        box_n=0,
    )
    stop = threading.Event()
    pumper = _drive_pump_in_background(listener, stop)
    try:
        shim.connect(timeout=2.0, proof={"shared_secret": "test-rack-secret"})
    finally:
        stop.set()
        pumper.join(timeout=1.0)

    tool_names = {t.name for t in shim.get_tools()}
    state_refs = {sr.name for sr in shim.get_state_refs()}
    channel_names = {c.name for c in shim.get_channels()}

    # Igor profile filters down to (inference + postgres + swadl)
    # ∩ online (only inference + postgres in fixture) = {inference, postgres}
    assert tool_names == {"inference", "postgres"}
    assert state_refs == {"twm", "ne", "milieu"}
    assert channel_names == {"shared", "igor-cc"}
    assert shim.get_primary_address() == "comms://testhost.0"
    assert shim.get_tool("inference") is not None
    assert shim.get_tool("nonexistent") is None


# ── BaseShim contract ─────────────────────────────────────────────────────────


def test_device_id_format(server, tmp_path):
    shim = IgorShim(
        instance_id="wild-0001",
        profiles_dir=tmp_path / "runtime",
    )
    assert shim.device_id == "igor-wild-0001"


def test_self_test_failed_when_not_connected(server, tmp_path):
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=tmp_path / "runtime",
    )
    result = shim.self_test()
    assert result["passed"] is False
    assert "not connected" in result["details"]


def test_self_test_passed_when_connected(server, listener, tmp_path):
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=tmp_path / "runtime",
        box="testhost",
        box_n=0,
    )
    stop = threading.Event()
    pumper = _drive_pump_in_background(listener, stop)
    try:
        shim.connect(timeout=2.0, proof={"shared_secret": "test-rack-secret"})
    finally:
        stop.set()
        pumper.join(timeout=1.0)
    result = shim.self_test()
    assert result["passed"] is True
    assert "manifest cached" in result["details"]


def test_start_stop_idempotent(server, tmp_path):
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=tmp_path / "runtime",
    )
    assert shim.start() is True
    assert shim.start() is True  # second call no-op
    assert shim.stop() is True
    assert shim.stop() is True
    assert shim.restart() is True


def test_rollback_drops_connection(server, listener, tmp_path):
    shim = IgorShim(
        instance_id="wild-test",
        profiles_dir=tmp_path / "runtime",
        box="testhost",
        box_n=0,
    )
    stop = threading.Event()
    pumper = _drive_pump_in_background(listener, stop)
    try:
        shim.connect(timeout=2.0, proof={"shared_secret": "test-rack-secret"})
    finally:
        stop.set()
        pumper.join(timeout=1.0)
    assert shim.client is not None
    shim.rollback()
    assert shim.client is None
    assert shim.manifest is None

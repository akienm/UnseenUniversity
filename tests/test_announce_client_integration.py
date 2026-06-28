"""
Slice 3 green-test: DatacenterClient + Skeleton end-to-end via IMAP stub.

Boots a real Skeleton with the canonical Igor profile and an IMAPServer
(stub mode), wires a DatacenterClient with an Igor IdentityEnvelope to
the same bus, calls client.announce(), drives skeleton.announce_pump()
once, and verifies the manifest is cached and queryable via the client's
accessor methods.

This ratifies that the agent-side and datacenter-side halves of the
announce protocol shipped together — slice 3 ships when both files
are green.
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
    AnnounceRejectedError,
    DatacenterClient,
    IdentityEnvelope,
)
from unseen_university.devices.skeleton.skeleton import Skeleton
from unseen_university.devices.bus.imap_server import IMAPServer
from unseen_university.devices.skeleton.registry import DeviceRegistry

CANONICAL_PROFILES = Path(__file__).parent.parent / "unseen_university" / "config" / "profiles"


@pytest.fixture()
def integration_rack(tmp_path: Path):
    """Live IMAPServer + Skeleton booted with the canonical Igor profile."""
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", profiles_dir / "igor.yaml")
    shutil.copy(CANONICAL_PROFILES / "cc.yaml", profiles_dir / "cc.yaml")

    server = IMAPServer()
    server.start()
    registry = DeviceRegistry(path=tmp_path / "devices.json")
    skel = Skeleton(registry=registry, imap_server=server, profiles_dir=profiles_dir)
    yield skel, server
    server.stop()


def _drive_pump_in_background(
    skel: Skeleton, stop: threading.Event
) -> threading.Thread:
    """Background pumper so client.announce()'s polling loop sees the reply."""

    def _run() -> None:
        while not stop.is_set():
            skel.announce_pump()
            time.sleep(0.02)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ── Slice 3 green test ────────────────────────────────────────────────────────


def test_client_round_trip_via_skeleton(integration_rack):
    skel, server = integration_rack

    igor_identity = IdentityEnvelope(
        agent_id="igor",
        instance="wild-0001",
        box="testhost",
        box_n=0,
        proof={"shared_secret": "test-rack-secret"},
        pid=8888,
        interface_version="1.0",
        surfaces=["console", "inference"],
    )
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        proof={"shared_secret": "test-rack-secret"},
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
    )

    stop = threading.Event()
    pumper = _drive_pump_in_background(skel, stop)
    try:
        manifest = client.announce(timeout=2.0)
    finally:
        stop.set()
        pumper.join(timeout=1.0)

    # Manifest cached on the client.
    assert client.manifest is manifest
    assert manifest["issued_to"]["agent_id"] == "igor"

    # Accessor methods return Igor's expected capability shape.
    tool_names = {t.name for t in client.get_tools()}
    # postgres + inference + swadl are online via the live registry tracking
    # skeleton's self-registration; igor.yaml allows them.
    # The skeleton-only registry won't have inference/postgres unless tests
    # register them — assert just on the inference, postgres, swadl SUBSET
    # filter rather than exact set.
    # (See: only skeleton is registered by default; allowed_devices list
    # is intersected with online registry.)
    assert tool_names.issubset(
        {"inference", "postgres", "browser_use", "swadl", "discord_bot", "web_server"}
    )

    state_ref_names = {sr.name for sr in client.get_state_refs()}
    assert state_ref_names == {"twm", "ne", "milieu"}

    channel_names = {c.name for c in client.get_channels()}
    assert channel_names == {"shared", "igor-cc"}

    # Suffix-style surface addresses from the announce protocol.
    assert client.get_surface_address("console") == "comms://testhost.0.console"
    assert client.get_surface_address("inference") == "comms://testhost.0.inference"
    assert client.get_primary_address() == "comms://testhost.0"

    # ACL came through.
    acl = client.get_acl()
    assert acl is not None
    assert acl.inbound_allow == ["*"]


def test_client_announce_unknown_agent_raises_via_error_envelope(integration_rack):
    """Posting an envelope for an agent without a profile triggers kind=error."""
    skel, server = integration_rack

    bad_identity = IdentityEnvelope(
        agent_id="ghost-agent",  # no ghost-agent.yaml in profiles dir
        instance="x",
        box="testhost",
        box_n=0,
        pid=1,
        interface_version="1.0",
    )
    client = DatacenterClient(
        agent_id=bad_identity.agent_id,
        instance=bad_identity.instance,
        box=bad_identity.box,
        box_n=bad_identity.box_n,
        pid=bad_identity.pid,
    )

    stop = threading.Event()
    pumper = _drive_pump_in_background(skel, stop)
    try:
        with pytest.raises(AnnounceRejectedError) as exc_info:
            client.announce(timeout=2.0)
    finally:
        stop.set()
        pumper.join(timeout=1.0)

    assert exc_info.value.error_kind == "resolve"


def test_client_round_trip_with_cc_profile(integration_rack):
    """A second profile (cc.yaml) round-trips the same way — broker is generic."""
    skel, server = integration_rack

    cc_identity = IdentityEnvelope(
        agent_id="cc",
        instance="session-1",
        box="testhost",
        box_n=1,
        pid=9999,
        interface_version="1.0",
        surfaces=["console", "mcp"],
        proof={"shared_secret": "test-rack-secret"},
    )
    client = DatacenterClient(
        agent_id=cc_identity.agent_id,
        instance=cc_identity.instance,
        box=cc_identity.box,
        box_n=cc_identity.box_n,
        pid=cc_identity.pid,
        surfaces=cc_identity.surfaces,
        proof=cc_identity.proof,
    )

    stop = threading.Event()
    pumper = _drive_pump_in_background(skel, stop)
    try:
        manifest = client.announce(timeout=2.0)
    finally:
        stop.set()
        pumper.join(timeout=1.0)

    assert manifest["issued_to"]["agent_id"] == "cc"
    # CC is stateless across sessions.
    assert client.get_state_refs() == []
    channel_names = {c.name for c in client.get_channels()}
    assert channel_names == {"shared", "igor-cc"}
    assert client.get_primary_address() == "comms://testhost.1"

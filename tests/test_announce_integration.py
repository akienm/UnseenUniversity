"""
Slice 2 green-test: end-to-end Skeleton + IMAP-stub round-trip.

Skeleton boots with an IMAP server, the announce + announce-events
mailboxes are created at init, an agent posts an IdentityEnvelope to
comms://announce, the listener pumps once, and a Manifest reply lands in
comms://announce-events with the agent's manifest_id.

This is the ticket that ratifies slice 2 has actually shipped end-to-end.
Slice 1's round-trip was in-memory broker-only; this one exercises the
full IMAP path.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Test mode must be set BEFORE bus.imap_server is imported.
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

import pytest

from unseen_university.announce import (
    ANNOUNCE_EVENTS_MAILBOX,
    ANNOUNCE_MAILBOX,
    IdentityEnvelope,
)
from unseen_university.skeleton.skeleton import Skeleton
from bus.envelope import Envelope
from bus.imap_server import IMAPServer
from skeleton.registry import DeviceRegistry

CANONICAL_PROFILES = Path(__file__).parent.parent / "config" / "profiles"


@pytest.fixture()
def integration_rack(tmp_path: Path):
    """Live IMAPServer + Skeleton booted with the canonical Igor profile."""
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", profiles_dir / "igor.yaml")

    server = IMAPServer()
    server.start()
    registry = DeviceRegistry(path=tmp_path / "devices.json")
    skel = Skeleton(registry=registry, imap_server=server, profiles_dir=profiles_dir)
    yield skel, server
    server.stop()


def _post_envelope(server: IMAPServer, identity: IdentityEnvelope) -> None:
    env = Envelope.now(
        from_device=identity.primary_mailbox,
        to_device="announce",
        payload=identity.to_dict(),
    )
    server.append(ANNOUNCE_MAILBOX, env)


# ── Slice 2 green test ────────────────────────────────────────────────────────


def test_round_trip_via_imap_stub(integration_rack):
    skel, server = integration_rack

    # Pre-conditions: announce mailboxes exist, both are empty.
    assert ANNOUNCE_MAILBOX in server.list_mailboxes()
    assert ANNOUNCE_EVENTS_MAILBOX in server.list_mailboxes()
    assert server.unseen_count(ANNOUNCE_EVENTS_MAILBOX) == 0

    # Agent posts its identity envelope.
    identity = IdentityEnvelope(
        agent_id="igor",
        instance="wild-0001",
        box="testhost",
        box_n=0,
        pid=12345,
        interface_version="1.0",
        surfaces=["console", "inference"],
    )
    _post_envelope(server, identity)
    assert server.unseen_count(ANNOUNCE_MAILBOX) == 1

    # Skeleton drives the listener once.
    processed = skel.announce_pump()
    assert processed == 1

    # Reply landed in announce-events with the expected shape.
    events = server.fetch_unseen(ANNOUNCE_EVENTS_MAILBOX)
    assert len(events) == 1
    reply = events[0]

    assert reply.from_device == "skeleton"
    assert reply.to_device == identity.primary_mailbox
    assert reply.payload["kind"] == "manifest"

    manifest = reply.payload["manifest"]
    assert manifest["issued_to"]["agent_id"] == "igor"
    assert manifest["issued_to"]["box"] == "testhost"
    assert manifest["issued_to"]["box_n"] == 0
    assert "manifest_id" in manifest
    assert len(manifest["manifest_id"]) > 0
    # profile_etag is a 64-char SHA-256 hex digest
    assert len(manifest["profile_etag"]) == 64

    # Surface addresses came back suffix-style.
    surfaces = manifest["surface_addresses"]
    assert surfaces.get("console") == "comms://testhost.0.console"
    assert surfaces.get("inference") == "comms://testhost.0.inference"


def test_malformed_envelope_returns_error_message(integration_rack):
    """A junk payload publishes a kind=error reply, listener does not crash."""
    skel, server = integration_rack

    junk = Envelope.now(
        from_device="anonymous",
        to_device="announce",
        payload={"this": "is", "not": "an", "identity": "envelope"},
    )
    server.append(ANNOUNCE_MAILBOX, junk)

    skel.announce_pump()
    events = server.fetch_unseen(ANNOUNCE_EVENTS_MAILBOX)
    assert len(events) == 1
    assert events[0].payload["kind"] == "error"
    assert "validation" in events[0].payload["error_kind"]

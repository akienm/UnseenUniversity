"""
AnnounceListener tests — pump() consumes envelopes from comms://announce
and publishes Manifest replies (or structured errors) to comms://announce-events.
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
    AnnounceBroker,
    AnnounceListener,
    IdentityEnvelope,
)
from unseen_university.devices.bus.envelope import Envelope
from unseen_university.devices.bus.imap_server import IMAPServer

CANONICAL_PROFILES = Path(__file__).parent.parent / "unseen_university" / "config" / "profiles"


@pytest.fixture()
def server():
    s = IMAPServer()
    s.start()
    s.create_mailbox(ANNOUNCE_MAILBOX)
    s.create_mailbox(ANNOUNCE_EVENTS_MAILBOX)
    yield s
    s.stop()


@pytest.fixture()
def profiles_dir(tmp_path: Path) -> Path:
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", tmp_path / "igor.yaml")
    return tmp_path


class _FakeRegistry:
    def list_devices(self):
        return [
            {"device_id": "inference", "status": "online"},
            {"device_id": "postgres", "status": "online"},
        ]


@pytest.fixture()
def listener(server, profiles_dir):
    broker = AnnounceBroker(
        profiles_dir=profiles_dir, registry=_FakeRegistry(), devices={}
    )
    return AnnounceListener(broker=broker, imap_server=server, from_device="skeleton")


def _send_announce(
    server: IMAPServer, payload: dict, from_device: str = "igor"
) -> None:
    env = Envelope.now(from_device=from_device, to_device="announce", payload=payload)
    server.append(ANNOUNCE_MAILBOX, env)


def _drain_events(server: IMAPServer) -> list[Envelope]:
    return server.fetch_unseen(ANNOUNCE_EVENTS_MAILBOX)


# ── Happy path ────────────────────────────────────────────────────────────────


def test_pump_publishes_manifest_for_valid_envelope(server, listener):
    identity = IdentityEnvelope(
        agent_id="igor",
        instance="wild-0001",
        box="testbox",
        box_n=0,
        pid=4242,
        interface_version="1.0",

        proof={"shared_secret": "test-rack-secret"},
        surfaces=["console"],
    )
    _send_announce(server, identity.to_dict())

    processed = listener.pump()
    assert processed == 1

    events = _drain_events(server)
    assert len(events) == 1
    reply = events[0]
    assert reply.payload["kind"] == "manifest"
    assert reply.payload["manifest"]["issued_to"]["agent_id"] == "igor"
    assert reply.to_device == "testbox.0"  # primary_mailbox


def test_pump_returns_zero_when_no_envelopes(server, listener):
    assert listener.pump() == 0
    assert _drain_events(server) == []


# ── Error paths ───────────────────────────────────────────────────────────────


def test_pump_publishes_error_for_missing_required_fields(server, listener):
    _send_announce(server, {"agent_id": "igor"})  # missing box/instance/etc.

    processed = listener.pump()
    assert processed == 1

    events = _drain_events(server)
    assert len(events) == 1
    reply = events[0]
    assert reply.payload["kind"] == "error"
    assert reply.payload["error_kind"] == "validation"


def test_pump_publishes_error_for_unknown_agent(server, profiles_dir):
    broker = AnnounceBroker(
        profiles_dir=profiles_dir, registry=_FakeRegistry(), devices={}
    )
    listener = AnnounceListener(
        broker=broker, imap_server=server, from_device="skeleton"
    )
    identity = IdentityEnvelope(
        agent_id="not-a-real-agent",
        instance="x",
        box="testbox",
        box_n=0,
        pid=1,
        interface_version="1.0",
    )
    _send_announce(server, identity.to_dict())

    processed = listener.pump()
    assert processed == 1
    events = _drain_events(server)
    assert events[0].payload["kind"] == "error"
    assert events[0].payload["error_kind"] == "resolve"


def test_pump_does_not_crash_on_garbage_payload(server, listener):
    """Junk payload publishes an error, not a stack trace."""
    _send_announce(server, {"this": "is", "not": "a", "valid": "envelope"})
    listener.pump()
    events = _drain_events(server)
    assert len(events) == 1
    assert events[0].payload["kind"] == "error"


# ── Multi-envelope batch ──────────────────────────────────────────────────────


def test_pump_processes_multiple_envelopes_in_one_call(server, listener):
    for n in range(3):
        identity = IdentityEnvelope(
            agent_id="igor",
            instance="wild-0001",
            box=f"box-{n}",
            box_n=n,
            pid=1000 + n,
            interface_version="1.0",

            proof={"shared_secret": "test-rack-secret"},
        )
        _send_announce(server, identity.to_dict())

    processed = listener.pump()
    assert processed == 3
    events = _drain_events(server)
    assert len(events) == 3
    assert all(e.payload["kind"] == "manifest" for e in events)

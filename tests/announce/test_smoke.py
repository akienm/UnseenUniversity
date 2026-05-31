"""
Smoke test: announce protocol end-to-end.

Agent constructs IdentityEnvelope → appends to comms://announce →
listener pumps → Manifest reply lands in comms://announce-events
with non-empty tool bindings.

Runs against the in-process IMAP stub (AGENT_DATACENTER_TEST_MODE=1).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Must be set before bus.imap_server is imported.
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

import pytest

from bus.envelope import Envelope
from bus.imap_server import IMAPServer
from unseen_university.announce import (
    ANNOUNCE_EVENTS_MAILBOX,
    ANNOUNCE_MAILBOX,
    AnnounceBroker,
    AnnounceListener,
    IdentityEnvelope,
)

CANONICAL_PROFILES = Path(__file__).parent.parent.parent / "config" / "profiles"


class _FakeRegistry:
    def list_devices(self):
        return [
            {"device_id": "inference", "status": "online"},
            {"device_id": "postgres", "status": "online"},
        ]


@pytest.fixture()
def rack(tmp_path: Path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", profiles_dir / "igor.yaml")

    server = IMAPServer()
    server.start()
    server.create_mailbox(ANNOUNCE_MAILBOX)
    server.create_mailbox(ANNOUNCE_EVENTS_MAILBOX)

    broker = AnnounceBroker(
        profiles_dir=profiles_dir,
        registry=_FakeRegistry(),
        devices={},
    )
    listener = AnnounceListener(
        broker=broker, imap_server=server, from_device="skeleton"
    )

    yield server, listener
    server.stop()


def test_announce_handshake_returns_manifest(rack):
    server, listener = rack

    def _pump():
        return listener.pump()

    identity = IdentityEnvelope(
        agent_id="igor",
        instance="wild-0001",
        box="testbox",
        box_n=0,
        pid=9999,
        interface_version="1.0",
        surfaces=["console", "inference"],
    )
    env = Envelope.now(
        from_device=identity.primary_mailbox,
        to_device="announce",
        payload=identity.to_dict(),
    )
    server.append(ANNOUNCE_MAILBOX, env)

    processed = _pump()
    assert processed == 1

    replies = server.fetch_unseen(ANNOUNCE_EVENTS_MAILBOX)
    assert len(replies) == 1

    reply = replies[0]
    assert reply.payload["kind"] == "manifest"
    manifest = reply.payload["manifest"]
    assert manifest["schema_version"] == "1.0"
    assert manifest["issued_to"]["agent_id"] == "igor"

    tools = manifest["tools"]
    assert len(tools) > 0, "Manifest must contain at least one bound tool"
    tool_names = {t["name"] for t in tools}
    assert "inference" in tool_names

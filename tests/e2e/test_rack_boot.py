"""
End-to-end: full rack boot sequence.

Validates the complete boot → announce → manifest flow:
  1. IMAPServer starts (in-process stub)
  2. Skeleton initialises with the bus, registers itself
  3. An agent announces its identity
  4. Skeleton.announce_pump() processes the envelope
  5. Manifest reply lands in announce-events with tool bindings
  6. Second device can announce independently and also gets a manifest

This is the minimal E2E — it does not require Postgres, network, or a
running uvicorn process.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from unseen_university.devices.bus.envelope import Envelope
from unseen_university.devices.bus.imap_server import IMAPServer
from unseen_university.announce import (
    ANNOUNCE_EVENTS_MAILBOX,
    ANNOUNCE_MAILBOX,
    IdentityEnvelope,
)
from unseen_university.devices.skeleton.skeleton import Skeleton

CANONICAL_PROFILES = Path(__file__).parent.parent.parent / "config" / "profiles"


@pytest.fixture
def profiles_dir(tmp_path):
    d = tmp_path / "profiles"
    d.mkdir()
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", d / "igor.yaml")
    shutil.copy(CANONICAL_PROFILES / "cc.yaml", d / "cc.yaml")
    return d


@pytest.fixture
def running_rack(tmp_path, profiles_dir):
    server = IMAPServer()
    server.start()
    skeleton = Skeleton(imap_server=server, profiles_dir=profiles_dir)
    yield server, skeleton
    server.stop()


# ── Boot ──────────────────────────────────────────────────────────────────────


def test_imap_server_starts(running_rack):
    server, _ = running_rack
    assert server.list_mailboxes()


def test_skeleton_creates_announce_mailboxes(running_rack):
    server, _ = running_rack
    mailboxes = server.list_mailboxes()
    assert ANNOUNCE_MAILBOX in mailboxes
    assert ANNOUNCE_EVENTS_MAILBOX in mailboxes


def test_skeleton_registers_itself(running_rack):
    _, skeleton = running_rack
    reg = skeleton._registry.get_device("skeleton")
    assert reg is not None


# ── Announce → Manifest ───────────────────────────────────────────────────────


_PROTECTED = frozenset({"igor", "cc", "skeleton"})


def _send_announce(server: IMAPServer, agent_id: str) -> None:
    identity = IdentityEnvelope(
        agent_id=agent_id,
        instance="e2e-0001",
        box="testbox",
        box_n=0,
        pid=9999,
        interface_version="1.0",
        surfaces=["console", "inference"],
        proof={"v": "e2e-test"} if agent_id in _PROTECTED else {},
    )
    env = Envelope.now(
        from_device=identity.primary_mailbox,
        to_device="announce",
        payload=identity.to_dict(),
    )
    server.append(ANNOUNCE_MAILBOX, env)


def test_announce_pump_returns_processed_count(running_rack):
    server, skeleton = running_rack
    _send_announce(server, "igor")
    processed = skeleton.announce_pump()
    assert processed == 1


def test_manifest_reply_lands_in_events_mailbox(running_rack):
    server, skeleton = running_rack
    _send_announce(server, "igor")
    skeleton.announce_pump()

    replies = server.fetch_unseen(ANNOUNCE_EVENTS_MAILBOX)
    assert len(replies) == 1
    assert replies[0].payload["kind"] == "manifest"


def test_manifest_has_tools_key(running_rack):
    server, skeleton = running_rack
    _send_announce(server, "igor")
    skeleton.announce_pump()

    reply = server.fetch_unseen(ANNOUNCE_EVENTS_MAILBOX)[0]
    manifest = reply.payload["manifest"]
    assert "tools" in manifest
    assert isinstance(manifest["tools"], list)


def test_manifest_schema_version(running_rack):
    server, skeleton = running_rack
    _send_announce(server, "igor")
    skeleton.announce_pump()

    reply = server.fetch_unseen(ANNOUNCE_EVENTS_MAILBOX)[0]
    assert reply.payload["manifest"]["schema_version"] == "1.0"


def test_manifest_issued_to_matches_agent(running_rack):
    server, skeleton = running_rack
    _send_announce(server, "igor")
    skeleton.announce_pump()

    reply = server.fetch_unseen(ANNOUNCE_EVENTS_MAILBOX)[0]
    assert reply.payload["manifest"]["issued_to"]["agent_id"] == "igor"


def test_second_agent_also_gets_manifest(running_rack):
    server, skeleton = running_rack

    _send_announce(server, "igor")
    _send_announce(server, "cc")
    processed = skeleton.announce_pump()

    assert processed == 2
    replies = server.fetch_unseen(ANNOUNCE_EVENTS_MAILBOX)
    assert len(replies) == 2
    issued_to = {r.payload["manifest"]["issued_to"]["agent_id"] for r in replies}
    assert "igor" in issued_to
    assert "cc" in issued_to


def test_pump_zero_when_mailbox_empty(running_rack):
    _, skeleton = running_rack
    assert skeleton.announce_pump() == 0

"""
Tests for T-swarm-channel-mechanism.

Verifies:
  - ChannelRegistry in-memory membership management
  - fan_out delivers to all members, skips non-members
  - shared channel always present
  - AnnounceListener registers subscriptions into ChannelRegistry after announce
  - Skeleton wires ChannelRegistry and creates shared mailbox
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

from unseen_university.announce.channels import ChannelRegistry
from bus.envelope import Envelope

CANONICAL_PROFILES = Path(__file__).parent.parent / "config" / "profiles"


# ── ChannelRegistry unit tests ─────────────────────────────────────────────────


def test_shared_channel_exists_on_init():
    reg = ChannelRegistry()
    assert "shared" in reg.channels()


def test_shared_has_no_members_on_init():
    reg = ChannelRegistry()
    assert reg.members("shared") == []


def test_register_member_adds_to_channel():
    reg = ChannelRegistry()
    reg.register_member("shared", "CC.0")
    assert "CC.0" in reg.members("shared")


def test_register_member_creates_channel_on_demand():
    reg = ChannelRegistry()
    reg.register_member("igor-cc", "igor-wild-0001")
    assert "igor-cc" in reg.channels()
    assert "igor-wild-0001" in reg.members("igor-cc")


def test_register_member_is_idempotent():
    reg = ChannelRegistry()
    reg.register_member("shared", "CC.0")
    reg.register_member("shared", "CC.0")
    assert reg.members("shared").count("CC.0") == 1


def test_unregister_member_removes_from_channel():
    reg = ChannelRegistry()
    reg.register_member("shared", "CC.0")
    reg.unregister_member("shared", "CC.0")
    assert "CC.0" not in reg.members("shared")


def test_unregister_member_noop_when_not_present():
    reg = ChannelRegistry()
    reg.unregister_member("shared", "nobody")  # should not raise


def test_members_returns_snapshot():
    reg = ChannelRegistry()
    reg.register_member("shared", "CC.0")
    snap = reg.members("shared")
    reg.register_member("shared", "igor-wild-0001")
    assert "igor-wild-0001" not in snap  # snapshot not affected


def test_unknown_channel_returns_empty_members():
    reg = ChannelRegistry()
    assert reg.members("no-such-channel") == []


# ── fan_out ───────────────────────────────────────────────────────────────────


def _env(from_device: str = "igor", to_device: str = "shared") -> Envelope:
    return Envelope.now(from_device=from_device, to_device=to_device)


def test_fan_out_delivers_to_all_members():
    reg = ChannelRegistry()
    reg.register_member("shared", "CC.0")
    reg.register_member("shared", "igor-wild-0001")

    imap = MagicMock()
    count = reg.fan_out("shared", _env(), imap)

    assert count == 2
    assert imap.append.call_count == 2
    called_mailboxes = {c.args[0] for c in imap.append.call_args_list}
    assert called_mailboxes == {"CC.0", "igor-wild-0001"}


def test_fan_out_empty_channel_returns_zero():
    reg = ChannelRegistry()
    imap = MagicMock()
    count = reg.fan_out("shared", _env(), imap)
    assert count == 0
    imap.append.assert_not_called()


def test_fan_out_unknown_channel_returns_zero():
    reg = ChannelRegistry()
    imap = MagicMock()
    count = reg.fan_out("no-such-channel", _env(), imap)
    assert count == 0


def test_fan_out_skips_failed_mailbox_and_continues():
    reg = ChannelRegistry()
    reg.register_member("shared", "CC.0")
    reg.register_member("shared", "igor-wild-0001")

    imap = MagicMock()
    imap.append.side_effect = [Exception("broken"), None]

    count = reg.fan_out("shared", _env(), imap)
    assert count == 1  # one failed, one succeeded


# ── Listener wires channel membership at announce time ────────────────────────


def test_listener_registers_subscriptions_after_announce(tmp_path: Path):
    """After a successful announce, agent's mailbox is in each subscribed channel."""
    from unseen_university.announce.broker import AnnounceBroker
    from unseen_university.announce.envelope import IdentityEnvelope
    from unseen_university.announce.listener import AnnounceListener
    from bus.imap_server import IMAPServer

    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", profiles_dir / "igor.yaml")

    server = IMAPServer()
    server.start()
    try:
        for mailbox in (
            "announce",
            "announce-events",
            "invalidate",
            "shared",
            "igor-wild-0001",
        ):
            server.create_mailbox(mailbox)

        broker = AnnounceBroker(
            profiles_dir=profiles_dir,
            registry=MagicMock(list_devices=lambda: []),
            devices={},
        )
        channel_reg = ChannelRegistry()
        listener = AnnounceListener(
            broker=broker,
            imap_server=server,
            channel_registry=channel_reg,
        )

        env = Envelope.now(
            from_device="testbox.0",
            to_device="announce",
            payload=IdentityEnvelope(
                agent_id="igor",
                instance="wild-0001",
                box="testbox",
                box_n=0,
                pid=1,
                interface_version="1.0",
            ).to_dict(),
        )
        server.append("announce", env)
        listener.pump()

        # igor profile has default_channels: [shared, igor-cc]
        # (broker guarantees shared is always present)
        assert "testbox.0" in channel_reg.members("shared")
        assert "testbox.0" in channel_reg.members("igor-cc")
    finally:
        server.stop()


# ── Skeleton wires ChannelRegistry and creates shared mailbox ─────────────────


def test_skeleton_creates_shared_mailbox_on_bootstrap(tmp_path: Path):
    from unseen_university.skeleton.skeleton import Skeleton
    from bus.imap_server import IMAPServer
    from skeleton.registry import DeviceRegistry

    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", profiles_dir / "igor.yaml")

    server = IMAPServer()
    server.start()
    try:
        registry = DeviceRegistry(path=tmp_path / "devices.json")
        skel = Skeleton(
            registry=registry, imap_server=server, profiles_dir=profiles_dir
        )
        assert "shared" in server.list_mailboxes()
    finally:
        server.stop()


def test_skeleton_exposes_channel_registry(tmp_path: Path):
    from unseen_university.skeleton.skeleton import Skeleton
    from bus.imap_server import IMAPServer
    from skeleton.registry import DeviceRegistry

    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", profiles_dir / "igor.yaml")

    server = IMAPServer()
    server.start()
    try:
        registry = DeviceRegistry(path=tmp_path / "devices.json")
        skel = Skeleton(
            registry=registry, imap_server=server, profiles_dir=profiles_dir
        )
        assert skel.channels is not None
        assert isinstance(skel.channels, ChannelRegistry)
        assert "shared" in skel.channels.channels()
    finally:
        server.stop()


def test_skeleton_without_bus_has_no_channel_registry():
    from unseen_university.skeleton.skeleton import Skeleton

    skel = Skeleton()
    assert skel.channels is None

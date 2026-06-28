"""
DatacenterClient unit tests — agent-side announce + manifest cache.

Covers announce success/timeout/error paths and accessor methods.
Integration with a real Skeleton lives in test_announce_client_integration.py.
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
    AnnounceRejectedError,
    AnnounceTimeoutError,
    DatacenterClient,
    IdentityEnvelope,
)
from unseen_university.devices.bus.envelope import Envelope
from unseen_university.devices.bus.imap_server import IMAPServer

CANONICAL_PROFILES = Path(__file__).parent.parent / "unseen_university" / "config" / "profiles"


# ── Fixtures ──────────────────────────────────────────────────────────────────


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
            {"device_id": "swadl", "status": "online"},
        ]


@pytest.fixture()
def listener(server, profiles_dir):
    broker = AnnounceBroker(
        profiles_dir=profiles_dir, registry=_FakeRegistry(), devices={}
    )
    return AnnounceListener(broker=broker, imap_server=server, from_device="skeleton")


@pytest.fixture()
def igor_identity() -> IdentityEnvelope:
    return IdentityEnvelope(
        agent_id="igor",
        instance="wild-0001",
        box="testbox",
        box_n=0,
        pid=4242,
        interface_version="1.0",
        surfaces=["console", "inference"],
        proof={"shared_secret": "test-rack-secret"},
    )


# ── Announce: happy path ──────────────────────────────────────────────────────


def test_announce_returns_manifest(server, listener, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )

    # Pump the listener after the client posts so the reply is ready when we poll.
    # Simplest pattern: post → pump → announce reads the existing reply.
    # But announce() posts internally. So we wire a dispatch closure.
    posted = client._imap.append  # save

    def post_then_pump(mailbox, env):
        posted(mailbox, env)
        if mailbox == ANNOUNCE_MAILBOX:
            listener.pump()

    client._imap.append = post_then_pump  # type: ignore[assignment]
    manifest = client.announce(timeout=2.0)
    client._imap.append = posted  # restore

    assert manifest["issued_to"]["agent_id"] == "igor"
    assert client.manifest is manifest  # cached


def test_announce_timeout_raises_when_no_listener(server, igor_identity):
    """No listener means no reply ever lands — must raise within the timeout."""
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    with pytest.raises(AnnounceTimeoutError):
        client.announce(timeout=0.2, poll_interval=0.05)


def test_announce_error_envelope_raises(server, listener, profiles_dir):
    """If the broker publishes kind=error, client raises AnnounceRejectedError."""
    bad_identity = IdentityEnvelope(
        agent_id="not-a-real-agent",  # no profile
        instance="x",
        box="testbox",
        box_n=0,
        pid=1,
        interface_version="1.0",
    )
    client = DatacenterClient(
        agent_id="not-a-real-agent", instance="x", box="testbox", box_n=0, pid=1
    )
    posted = client._imap.append

    def post_then_pump(mailbox, env):
        posted(mailbox, env)
        if mailbox == ANNOUNCE_MAILBOX:
            listener.pump()

    client._imap.append = post_then_pump  # type: ignore[assignment]
    with pytest.raises(AnnounceRejectedError) as exc_info:
        client.announce(timeout=2.0)
    client._imap.append = posted

    assert exc_info.value.error_kind == "resolve"


# ── Manifest accessors (manual fixture) ───────────────────────────────────────


def _seed_manifest(client: DatacenterClient) -> None:
    """Hand the client a synthetic manifest without going through the broker."""
    client._manifest = {
        "tools": [
            {
                "name": "inference",
                "address": "comms://inference",
                "interface": "imap_envelope",
                "input_schema": {},
                "output_schema": None,
                "permission_mode": "read_write",
                "rate_limit_per_min": 60,
                "description": "LLM inference",
            },
            {
                "name": "postgres",
                "address": "comms://postgres",
                "interface": "imap_envelope",
                "input_schema": {},
                "output_schema": None,
                "permission_mode": "read_write",
                "rate_limit_per_min": None,
                "description": "Postgres",
            },
        ],
        "subscriptions": [
            {
                "name": "shared",
                "address": "comms://shared",
                "role": "member",
                "notify_on_intent": True,
            }
        ],
        "state_refs": [
            {"name": "twm", "uri": "postgres://...#twm", "mode": "read_write"}
        ],
        "acl": {
            "inbound_allow": ["*"],
            "inbound_deny": [],
            "outbound_allow": ["*"],
            "outbound_deny": [],
        },
        "surface_addresses": {
            "console": "comms://testbox.0.console",
            "inference": "comms://testbox.0.inference",
        },
        "primary_address": "comms://testbox.0",
    }


def test_manifest_property_none_before_announce(server, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    assert client.manifest is None


def test_get_tool_returns_binding_or_none(server, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    _seed_manifest(client)
    assert client.get_tool("inference").name == "inference"
    assert client.get_tool("inference").rate_limit_per_min == 60
    assert client.get_tool("nonexistent") is None


def test_get_tools_returns_full_list(server, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    _seed_manifest(client)
    names = {t.name for t in client.get_tools()}
    assert names == {"inference", "postgres"}


def test_get_state_ref_returns_ref_or_none(server, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    _seed_manifest(client)
    assert client.get_state_ref("twm").mode == "read_write"
    assert client.get_state_ref("nonexistent") is None


def test_get_channels_returns_subscriptions(server, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    _seed_manifest(client)
    channels = client.get_channels()
    assert len(channels) == 1
    assert channels[0].name == "shared"


def test_get_acl_returns_acl_dataclass(server, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    _seed_manifest(client)
    acl = client.get_acl()
    assert acl is not None
    assert acl.inbound_allow == ["*"]


def test_get_surface_address_returns_address_or_none(server, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    _seed_manifest(client)
    assert client.get_surface_address("console") == "comms://testbox.0.console"
    assert client.get_surface_address("nonexistent") is None


def test_get_primary_address(server, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    _seed_manifest(client)
    assert client.get_primary_address() == "comms://testbox.0"


def test_accessors_return_empty_when_no_manifest(server, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    assert client.get_tools() == []
    assert client.get_state_refs() == []
    assert client.get_channels() == []
    assert client.get_acl() is None
    assert client.get_primary_address() is None


# ── Reply targeting ───────────────────────────────────────────────────────────


def test_announce_ignores_replies_addressed_to_other_agents(server, listener):
    """If two clients share the announce-events mailbox, each only takes its own."""
    other_reply = Envelope.now(
        from_device="skeleton",
        to_device="someone-elses.0",
        payload={"kind": "manifest", "manifest": {"issued_to": {}}},
    )
    server.append(ANNOUNCE_EVENTS_MAILBOX, other_reply)

    igor_identity = IdentityEnvelope(
        agent_id="igor",
        instance="wild-0001",
        box="testbox",
        box_n=0,
        pid=4242,
        interface_version="1.0",
        proof={"shared_secret": "test-rack-secret"},
    )
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    posted = client._imap.append

    def post_then_pump(mailbox, env):
        posted(mailbox, env)
        if mailbox == ANNOUNCE_MAILBOX:
            listener.pump()

    client._imap.append = post_then_pump  # type: ignore[assignment]
    manifest = client.announce(timeout=2.0)
    client._imap.append = posted

    # Got our own manifest, not the bystander reply.
    assert manifest["issued_to"]["agent_id"] == "igor"


# ── check_for_invalidate (slice 3b) ───────────────────────────────────────────


def _post_invalidate(server, target, reason="changed"):
    """Helper: drop a kind=invalidate envelope onto comms://invalidate."""
    from unseen_university.announce.manifest import INVALIDATE_MAILBOX

    server.create_mailbox(INVALIDATE_MAILBOX)  # idempotent
    env = Envelope.now(
        from_device="invalidator",
        to_device=INVALIDATE_MAILBOX,
        payload={"kind": "invalidate", "target": target, "reason": reason},
    )
    server.append(INVALIDATE_MAILBOX, env)


def test_check_for_invalidate_zero_when_no_envelopes(server, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    assert client.check_for_invalidate() == 0


def test_invalidate_for_our_agent_triggers_reannounce(server, listener, igor_identity):
    """Post an invalidate for igor; check_for_invalidate re-announces."""
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    posted = client._imap.append

    def post_then_pump(mailbox, env):
        posted(mailbox, env)
        if mailbox == ANNOUNCE_MAILBOX:
            listener.pump()

    client._imap.append = post_then_pump  # type: ignore[assignment]
    try:
        # Initial announce.
        first = client.announce(timeout=2.0)
        first_id = first["manifest_id"]

        _post_invalidate(server, target="igor", reason="changed")
        handled = client.check_for_invalidate(reannounce_timeout=2.0)
        assert handled == 1

        # Cached manifest replaced — manifest_id is fresh.
        assert client.manifest is not None
        assert client.manifest["manifest_id"] != first_id
    finally:
        client._imap.append = posted


def test_invalidate_for_registry_triggers_reannounce(server, listener, igor_identity):
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    posted = client._imap.append

    def post_then_pump(mailbox, env):
        posted(mailbox, env)
        if mailbox == ANNOUNCE_MAILBOX:
            listener.pump()

    client._imap.append = post_then_pump  # type: ignore[assignment]
    try:
        first = client.announce(timeout=2.0)
        _post_invalidate(server, target="registry", reason="changed")
        handled = client.check_for_invalidate(reannounce_timeout=2.0)
        assert handled == 1
        assert client.manifest["manifest_id"] != first["manifest_id"]
    finally:
        client._imap.append = posted


def test_invalidate_for_other_agent_ignored(server, listener, igor_identity):
    """target=cc when we are igor → no re-announce, manifest unchanged."""
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    posted = client._imap.append

    def post_then_pump(mailbox, env):
        posted(mailbox, env)
        if mailbox == ANNOUNCE_MAILBOX:
            listener.pump()

    client._imap.append = post_then_pump  # type: ignore[assignment]
    try:
        first = client.announce(timeout=2.0)
        first_id = first["manifest_id"]
        _post_invalidate(server, target="cc", reason="changed")
        handled = client.check_for_invalidate(reannounce_timeout=2.0)
        assert handled == 0
        # Manifest unchanged.
        assert client.manifest["manifest_id"] == first_id
    finally:
        client._imap.append = posted


def test_check_for_invalidate_handles_reannounce_timeout_gracefully(
    server, listener, igor_identity
):
    """Re-announce times out → keep stale manifest, return 0, no exception."""
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    posted = client._imap.append

    def post_then_pump(mailbox, env):
        posted(mailbox, env)
        if mailbox == ANNOUNCE_MAILBOX:
            listener.pump()

    client._imap.append = post_then_pump  # type: ignore[assignment]
    try:
        first = client.announce(timeout=2.0)
        # Take the listener offline so re-announce can't get a manifest.
        client._imap.append = posted  # restore — no more pumping
        _post_invalidate(server, target="igor", reason="changed")
        handled = client.check_for_invalidate(
            reannounce_timeout=0.2, reannounce_poll_interval=0.05
        )
        assert handled == 0
        # Stale manifest preserved.
        assert client.manifest["manifest_id"] == first["manifest_id"]
    finally:
        client._imap.append = posted


def test_invalidate_coalesces_multiple_envelopes_in_one_pump(
    server, listener, igor_identity
):
    """3 invalidates posted at once → one re-announce satisfies them all."""
    client = DatacenterClient(
        agent_id=igor_identity.agent_id,
        instance=igor_identity.instance,
        box=igor_identity.box,
        box_n=igor_identity.box_n,
        pid=igor_identity.pid,
        surfaces=igor_identity.surfaces,
        proof=igor_identity.proof,
    )
    posted = client._imap.append

    def post_then_pump(mailbox, env):
        posted(mailbox, env)
        if mailbox == ANNOUNCE_MAILBOX:
            listener.pump()

    client._imap.append = post_then_pump  # type: ignore[assignment]
    try:
        client.announce(timeout=2.0)
        _post_invalidate(server, target="igor")
        _post_invalidate(server, target="registry")
        _post_invalidate(server, target="igor")
        handled = client.check_for_invalidate(reannounce_timeout=2.0)
        # Coalesced: still just one re-announce.
        assert handled == 1
    finally:
        client._imap.append = posted

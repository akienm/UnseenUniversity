"""
Round-trip test for announce-protocol slice 1.

Green = slice 1 ships. Tests envelope → broker → profile resolution →
manifest assembly end-to-end, in-process, no IMAP.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from unseen_university.announce.broker import AnnounceError, AnnounceBroker
from unseen_university.announce.envelope import (
    ANNOUNCE_MAILBOX,
    IdentityEnvelope,
    ValidationError,
)
from unseen_university.announce.manifest import MANIFEST_SCHEMA_VERSION, Manifest
from unseen_university.announce.profile import ProfileNotFoundError

# ── Fixtures ──────────────────────────────────────────────────────────────────

CANONICAL_PROFILES = Path(__file__).parent.parent / "unseen_university" / "config" / "profiles"


class _FakeDevice:
    def __init__(self, device_id: str, address: str, name: str = "") -> None:
        self.device_id = device_id
        self._address = address
        self._name = name or device_id

    def who_am_i(self) -> dict:
        return {"name": self._name}

    def comms(self) -> dict:
        return {"address": self._address, "mode": "read_write"}


class _FakeRegistry:
    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries

    def list_devices(self) -> list[dict]:
        return self._entries


@pytest.fixture()
def profiles_dir(tmp_path: Path) -> Path:
    """Temp directory pre-seeded with the canonical igor.yaml."""
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", tmp_path / "igor.yaml")
    return tmp_path


@pytest.fixture()
def igor_envelope() -> IdentityEnvelope:
    return IdentityEnvelope(
        agent_id="igor",
        instance="wild-0001",
        box="testbox",
        box_n=0,
        pid=9999,
        interface_version="1.0",
        proof={"shared_secret": "test-rack-secret"},
        surfaces=["console", "inference"],
    )


@pytest.fixture()
def broker(profiles_dir: Path) -> AnnounceBroker:
    registry = _FakeRegistry(
        [
            {"device_id": "inference", "status": "online"},
            {"device_id": "postgres", "status": "online"},
            {"device_id": "browser_use", "status": "offline"},
            {"device_id": "swadl", "status": "online"},
        ]
    )
    devices = {
        "inference": _FakeDevice("inference", "comms://inference", "Inference"),
        "postgres": _FakeDevice("postgres", "comms://postgres", "Postgres"),
        "swadl": _FakeDevice("swadl", "comms://swadl", "SWADL"),
    }
    return AnnounceBroker(profiles_dir=profiles_dir, registry=registry, devices=devices)


# ── Happy-path round-trip ─────────────────────────────────────────────────────


def test_round_trip_returns_manifest(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    manifest = broker.resolve_announce(igor_envelope)
    assert isinstance(manifest, Manifest)
    assert manifest.schema_version == MANIFEST_SCHEMA_VERSION


def test_round_trip_issued_to_matches_envelope(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    manifest = broker.resolve_announce(igor_envelope)
    assert manifest.issued_to["agent_id"] == "igor"
    assert manifest.issued_to["instance"] == "wild-0001"
    assert manifest.issued_to["box"] == "testbox"
    assert manifest.issued_to["box_n"] == 0


def test_round_trip_tools_include_online_allowed_devices(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    manifest = broker.resolve_announce(igor_envelope)
    tool_names = {t.name for t in manifest.tools}
    assert "inference" in tool_names
    assert "postgres" in tool_names
    assert "swadl" in tool_names


def test_round_trip_excludes_offline_devices(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    manifest = broker.resolve_announce(igor_envelope)
    tool_names = {t.name for t in manifest.tools}
    assert "browser_use" not in tool_names


def test_round_trip_channels(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    manifest = broker.resolve_announce(igor_envelope)
    channel_names = {s.name for s in manifest.subscriptions}
    assert "shared" in channel_names
    assert "igor-cc" in channel_names


def test_round_trip_state_refs(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    manifest = broker.resolve_announce(igor_envelope)
    ref_names = {sr.name for sr in manifest.state_refs}
    assert ref_names == {"twm", "ne", "milieu"}


def test_round_trip_primary_address(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    manifest = broker.resolve_announce(igor_envelope)
    assert manifest.primary_address == "comms://testbox.0"


def test_round_trip_surface_addresses(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    manifest = broker.resolve_announce(igor_envelope)
    assert manifest.surface_addresses.get("console") == "comms://testbox.0.console"
    assert manifest.surface_addresses.get("inference") == "comms://testbox.0.inference"


def test_round_trip_acl(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    manifest = broker.resolve_announce(igor_envelope)
    assert manifest.acl.inbound_allow == ["*"]
    assert manifest.acl.inbound_deny == []


def test_round_trip_manifest_id_unique(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    m1 = broker.resolve_announce(igor_envelope)
    m2 = broker.resolve_announce(igor_envelope)
    assert m1.manifest_id != m2.manifest_id


def test_round_trip_etags_present(
    broker: AnnounceBroker, igor_envelope: IdentityEnvelope
) -> None:
    manifest = broker.resolve_announce(igor_envelope)
    assert len(manifest.profile_etag) == 64  # SHA-256 hex
    assert len(manifest.registry_etag) == 64


# ── Error paths ───────────────────────────────────────────────────────────────


def test_missing_profile_raises_announce_error(profiles_dir: Path) -> None:
    broker = AnnounceBroker(
        profiles_dir=profiles_dir, registry=_FakeRegistry([]), devices={}
    )
    env = IdentityEnvelope(
        agent_id="unknown-agent",
        instance="x",
        box="testbox",
        box_n=0,
        pid=1,
        interface_version="1.0",
    )
    with pytest.raises(AnnounceError):
        broker.resolve_announce(env)


def test_envelope_missing_required_field_raises() -> None:
    with pytest.raises(ValidationError):
        IdentityEnvelope.from_dict({"agent_id": "igor"})


def test_broker_with_no_registry_returns_empty_tools(
    profiles_dir: Path, igor_envelope: IdentityEnvelope
) -> None:
    broker = AnnounceBroker(profiles_dir=profiles_dir, registry=None, devices={})
    manifest = broker.resolve_announce(igor_envelope)
    assert manifest.tools == []


# ── Constants ─────────────────────────────────────────────────────────────────


def test_announce_mailbox_constant() -> None:
    assert ANNOUNCE_MAILBOX == "announce"


# ── Additional canonical profiles (slice 2) ───────────────────────────────────


def _profiles_dir_with(tmp_path: Path, *names: str) -> Path:
    for name in names:
        shutil.copy(CANONICAL_PROFILES / name, tmp_path / name)
    return tmp_path


def test_cc_profile_loads_and_has_expected_shape(tmp_path: Path) -> None:
    from unseen_university.announce.profile import load_profile

    pdir = _profiles_dir_with(tmp_path, "cc.yaml")
    profile = load_profile("cc", profiles_dir=pdir)

    assert profile["agent_type"] == "cc"
    assert "inference" in profile["allowed_devices"]
    assert "postgres" not in profile["allowed_devices"]  # CC is stateless
    assert "shared" in profile["default_channels"]
    assert "igor-cc" in profile["default_channels"]
    assert profile["state_refs"] == {}
    assert "skeleton" in profile["acl"]["inbound"]["allow"]
    assert profile["surfaces"]["mcp"] is True


def test_research_orca_profile_loads_and_has_expected_shape(tmp_path: Path) -> None:
    from unseen_university.announce.profile import load_profile

    pdir = _profiles_dir_with(tmp_path, "research-orca.yaml")
    profile = load_profile("research-orca", profiles_dir=pdir)

    assert profile["agent_type"] == "research-orca"
    assert profile["allowed_devices"] == ["inference", "browser_use"]
    assert profile["default_channels"] == ["shared"]
    assert profile["state_refs"] == {}
    assert profile["acl"]["inbound"]["allow"] == ["skeleton", "cc", "igor"]
    assert profile["acl"]["outbound"]["allow"] == ["shared", "cc"]
    assert profile["surfaces"]["mcp"] is False


def test_broker_resolves_research_orca_with_narrow_devices(tmp_path: Path) -> None:
    from unseen_university.announce.broker import AnnounceBroker
    from unseen_university.announce.envelope import IdentityEnvelope

    pdir = _profiles_dir_with(tmp_path, "research-orca.yaml")
    registry = _FakeRegistry(
        [
            {"device_id": "inference", "status": "online"},
            {"device_id": "browser_use", "status": "online"},
            {"device_id": "postgres", "status": "online"},  # online but not allowed
        ]
    )
    devices = {
        "inference": _FakeDevice("inference", "comms://inference"),
        "browser_use": _FakeDevice("browser_use", "comms://browser_use"),
        "postgres": _FakeDevice("postgres", "comms://postgres"),
    }
    broker = AnnounceBroker(profiles_dir=pdir, registry=registry, devices=devices)
    env = IdentityEnvelope(
        agent_id="research-orca",
        instance="orca-1",
        box="researchhost",
        box_n=2,
        pid=8888,
        interface_version="1.0",
    )
    manifest = broker.resolve_announce(env)
    tool_names = {t.name for t in manifest.tools}
    assert tool_names == {"inference", "browser_use"}  # postgres excluded

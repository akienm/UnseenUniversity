"""
Tests for T-swarm-visibility-rules.

  - visibility field flows from profile YAML → Manifest
  - default visibility is "secondary" when not set in profile
  - "shared" channel is guaranteed in subscriptions even when absent from default_channels
  - cc.yaml and igor.yaml declare visibility: primary
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from agent_datacenter.announce.broker import AnnounceBroker, ManifestAssembler
from agent_datacenter.announce.envelope import IdentityEnvelope
from agent_datacenter.announce.manifest import Manifest

CANONICAL_PROFILES = Path(__file__).parent.parent / "config" / "profiles"


# ── helpers ───────────────────────────────────────────────────────────────────


class _FakeDevice:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id

    def who_am_i(self) -> dict:
        return {"name": self.device_id}

    def comms(self) -> dict:
        return {"address": f"comms://{self.device_id}", "mode": "read_write"}


class _FakeRegistry:
    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries

    def list_devices(self) -> list[dict]:
        return self._entries


def _write_profile(tmp_path: Path, name: str, extra: str = "") -> Path:
    """Write a minimal valid profile YAML to tmp_path."""
    base = textwrap.dedent(f"""\
        profile_version: "1.0"
        agent_type: {name}
        description: test profile
        allowed_devices: []
        device_permissions: {{}}
        default_channels:
          - {name}-channel
        state_refs: {{}}
        acl:
          inbound:
            allow: ["*"]
            deny: []
          outbound:
            allow: ["*"]
            deny: []
        surfaces:
          console: true
        """)
    text = base + extra
    path = tmp_path / f"{name}.yaml"
    path.write_text(text)
    return tmp_path


def _broker(profiles_dir: Path) -> AnnounceBroker:
    return AnnounceBroker(
        profiles_dir=profiles_dir, registry=_FakeRegistry([]), devices={}
    )


def _envelope(agent_id: str) -> IdentityEnvelope:
    return IdentityEnvelope(
        agent_id=agent_id,
        instance="test-0",
        box="testbox",
        box_n=0,
        pid=1,
        interface_version="1.0",
    )


# ── visibility field: profile → manifest ─────────────────────────────────────


def test_visibility_primary_flows_to_manifest(tmp_path: Path) -> None:
    pdir = _write_profile(tmp_path, "alice", "visibility: primary\n")
    manifest = _broker(pdir).resolve_announce(_envelope("alice"))
    assert manifest.visibility == "primary"


def test_visibility_secondary_flows_to_manifest(tmp_path: Path) -> None:
    pdir = _write_profile(tmp_path, "bob", "visibility: secondary\n")
    manifest = _broker(pdir).resolve_announce(_envelope("bob"))
    assert manifest.visibility == "secondary"


def test_visibility_defaults_to_secondary_when_not_in_profile(tmp_path: Path) -> None:
    pdir = _write_profile(tmp_path, "carol")  # no visibility key
    manifest = _broker(pdir).resolve_announce(_envelope("carol"))
    assert manifest.visibility == "secondary"


def test_manifest_visibility_default_is_secondary() -> None:
    """Manifest dataclass default must be 'secondary' so old callers don't break."""
    m = Manifest(
        schema_version="1.0",
        issued_at="2026-01-01T00:00:00+00:00",
        issued_by="skeleton@test",
        issued_to={"agent_id": "x", "instance": "0", "box": "b", "box_n": 0},
        manifest_id="some-uuid",
        tools=[],
        subscriptions=[],
        state_refs=[],
        acl=None,
        surface_addresses={},
        primary_address="comms://x",
        profile_version="1.0",
        profile_etag="abc",
        registry_etag="def",
    )
    assert m.visibility == "secondary"


# ── shared channel guarantee ──────────────────────────────────────────────────


def test_shared_injected_when_missing_from_default_channels(tmp_path: Path) -> None:
    pdir = _write_profile(tmp_path, "dave")  # default_channels has only dave-channel
    manifest = _broker(pdir).resolve_announce(_envelope("dave"))
    names = [s.name for s in manifest.subscriptions]
    assert "shared" in names


def test_shared_first_when_injected(tmp_path: Path) -> None:
    pdir = _write_profile(tmp_path, "eve")
    manifest = _broker(pdir).resolve_announce(_envelope("eve"))
    assert manifest.subscriptions[0].name == "shared"


def test_shared_not_duplicated_when_already_in_channels(tmp_path: Path) -> None:
    extra = textwrap.dedent("""\
        default_channels:
          - shared
          - frank-channel
        """)
    pdir = _write_profile(tmp_path, "frank", extra)
    manifest = _broker(pdir).resolve_announce(_envelope("frank"))
    shared_count = sum(1 for s in manifest.subscriptions if s.name == "shared")
    assert shared_count == 1


def test_shared_address_is_comms_shared(tmp_path: Path) -> None:
    pdir = _write_profile(tmp_path, "grace")
    manifest = _broker(pdir).resolve_announce(_envelope("grace"))
    shared = next(s for s in manifest.subscriptions if s.name == "shared")
    assert shared.address == "comms://shared"


# ── canonical profiles declare visibility: primary ────────────────────────────


def test_cc_profile_has_visibility_primary(tmp_path: Path) -> None:
    from agent_datacenter.announce.profile import load_profile

    shutil.copy(CANONICAL_PROFILES / "cc.yaml", tmp_path / "cc.yaml")
    profile = load_profile("cc", profiles_dir=tmp_path)
    assert profile.get("visibility") == "primary"


def test_igor_profile_has_visibility_primary(tmp_path: Path) -> None:
    from agent_datacenter.announce.profile import load_profile

    shutil.copy(CANONICAL_PROFILES / "igor.yaml", tmp_path / "igor.yaml")
    profile = load_profile("igor", profiles_dir=tmp_path)
    assert profile.get("visibility") == "primary"

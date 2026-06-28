"""
AnnounceMcpServer (CC-side adapter) tests — drive the three MCP-style tools
against a booted in-process Skeleton via IMAP stub.
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

from unseen_university.announce import AnnounceMcpServer
from unseen_university.announce.manifest import INVALIDATE_MAILBOX
from unseen_university.devices.skeleton.skeleton import Skeleton
from unseen_university.devices.bus.envelope import Envelope
from unseen_university.devices.bus.imap_server import IMAPServer
from unseen_university.devices.skeleton.registry import DeviceRegistry

CANONICAL_PROFILES = Path(__file__).parent.parent / "unseen_university" / "config" / "profiles"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def integration_rack(tmp_path: Path):
    """Real Skeleton + IMAPServer with cc.yaml + igor.yaml + research-orca.yaml."""
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    for yml in ("cc.yaml", "igor.yaml", "research-orca.yaml"):
        shutil.copy(CANONICAL_PROFILES / yml, profiles_dir / yml)

    server = IMAPServer()
    server.start()
    registry = DeviceRegistry(path=tmp_path / "devices.json")
    skel = Skeleton(registry=registry, imap_server=server, profiles_dir=profiles_dir)
    yield skel, server
    server.stop()


def _drive_pump(skel, stop):
    def _run():
        while not stop.is_set():
            skel.announce_pump()
            time.sleep(0.02)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ── announce_tool ────────────────────────────────────────────────────────────


def test_announce_tool_returns_manifest_dict(integration_rack):
    skel, server = integration_rack
    adapter = AnnounceMcpServer(
        instance_id="cc-test-1",
        agent_id="cc",
        box="testhost",
        box_n=2,

        proof={"shared_secret": "test-rack-secret"},
    )

    stop = threading.Event()
    pumper = _drive_pump(skel, stop)
    try:
        result = adapter.announce_tool(timeout=2.0)
    finally:
        stop.set()
        pumper.join(timeout=1.0)

    assert result["ok"] is True
    manifest = result["manifest"]
    assert manifest["issued_to"]["agent_id"] == "cc"
    assert manifest["primary_address"] == "comms://testhost.2"


def test_announce_tool_returns_error_dict_on_unknown_agent(integration_rack):
    """Unknown agent_id → broker publishes kind=error → adapter returns error dict."""
    skel, server = integration_rack
    adapter = AnnounceMcpServer(
        instance_id="ghost-1",
        agent_id="ghost-agent",  # no profile shipped
        box="testhost",
    )
    stop = threading.Event()
    pumper = _drive_pump(skel, stop)
    try:
        result = adapter.announce_tool(timeout=2.0)
    finally:
        stop.set()
        pumper.join(timeout=1.0)

    assert result["ok"] is False
    assert "rejected" in result["error"]
    assert result["error_kind"] == "resolve"


def test_announce_tool_returns_timeout_error_when_no_listener():
    """No skeleton, no listener → announce times out → error dict."""
    adapter = AnnounceMcpServer(
        instance_id="orphan",
        agent_id="cc",
    )
    result = adapter.announce_tool(timeout=0.2)
    assert result["ok"] is False
    assert "timed out" in result["error"]


# ── manifest_tool ────────────────────────────────────────────────────────────


def test_manifest_tool_returns_none_before_announce():
    adapter = AnnounceMcpServer()
    result = adapter.manifest_tool()
    assert result == {"ok": True, "manifest": None}


def test_manifest_tool_returns_cached_after_announce(integration_rack):
    skel, server = integration_rack
    adapter = AnnounceMcpServer(
        instance_id="cc-test-2",
        agent_id="cc",
        box="testhost",
        box_n=3,

        proof={"shared_secret": "test-rack-secret"},
    )
    stop = threading.Event()
    pumper = _drive_pump(skel, stop)
    try:
        adapter.announce_tool(timeout=2.0)
    finally:
        stop.set()
        pumper.join(timeout=1.0)

    result = adapter.manifest_tool()
    assert result["ok"] is True
    assert result["manifest"]["issued_to"]["agent_id"] == "cc"


# ── check_for_invalidate_tool ────────────────────────────────────────────────


def test_check_for_invalidate_tool_returns_zero_when_idle(integration_rack):
    skel, server = integration_rack
    adapter = AnnounceMcpServer(
        instance_id="cc-test-3",
        agent_id="cc",
        box="testhost",
    )
    result = adapter.check_for_invalidate_tool(reannounce_timeout=0.2)
    assert result["ok"] is True
    assert result["handled"] == 0


def test_check_for_invalidate_tool_handles_matching_envelope(integration_rack):
    skel, server = integration_rack
    adapter = AnnounceMcpServer(
        instance_id="cc-test-4",
        agent_id="cc",
        box="testhost",
        box_n=4,

        proof={"shared_secret": "test-rack-secret"},
    )
    stop = threading.Event()
    pumper = _drive_pump(skel, stop)
    try:
        adapter.announce_tool(timeout=2.0)

        # Drop a matching invalidate.
        env = Envelope.now(
            from_device="invalidator",
            to_device=INVALIDATE_MAILBOX,
            payload={"kind": "invalidate", "target": "cc", "reason": "changed"},
        )
        server.append(INVALIDATE_MAILBOX, env)

        result = adapter.check_for_invalidate_tool(reannounce_timeout=2.0)
    finally:
        stop.set()
        pumper.join(timeout=1.0)

    assert result["ok"] is True
    assert result["handled"] == 1
    assert result["manifest"]["issued_to"]["agent_id"] == "cc"


# ── Singleton-ish behavior + identity inspection ─────────────────────────────


def test_singleton_reuse_across_calls(integration_rack):
    """Adapter holds one client; announce + manifest + check all share it."""
    skel, server = integration_rack
    adapter = AnnounceMcpServer(
        instance_id="cc-test-5",
        agent_id="cc",
        box="testhost",
        box_n=5,

        proof={"shared_secret": "test-rack-secret"},
    )
    initial_client = adapter.client

    stop = threading.Event()
    pumper = _drive_pump(skel, stop)
    try:
        adapter.announce_tool(timeout=2.0)
    finally:
        stop.set()
        pumper.join(timeout=1.0)

    # Same client object after announce.
    assert adapter.client is initial_client
    # Manifest tool reads from the same client.
    assert adapter.manifest_tool()["manifest"] is initial_client.manifest


def test_identity_defaults_use_hostname_and_pid():
    adapter = AnnounceMcpServer()
    identity = adapter.identity
    assert identity.agent_id == "cc"
    assert identity.box  # non-empty hostname
    assert identity.pid == os.getpid()
    assert "console" in identity.surfaces
    assert "mcp" in identity.surfaces

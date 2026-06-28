"""
Invalidator tests — polling diff over profiles_dir + registry, publishes
kind=invalidate envelopes to comms://announce-events.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

# Test mode must be set BEFORE bus.imap_server is imported.
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

import pytest

from unseen_university.announce import Invalidator
from unseen_university.announce.manifest import INVALIDATE_MAILBOX
from unseen_university.devices.bus.imap_server import IMAPServer

CANONICAL_PROFILES = Path(__file__).parent.parent / "unseen_university" / "config" / "profiles"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def server():
    s = IMAPServer()
    s.start()
    s.create_mailbox(INVALIDATE_MAILBOX)
    yield s
    s.stop()


@pytest.fixture()
def profiles_dir(tmp_path: Path) -> Path:
    d = tmp_path / "profiles"
    d.mkdir()
    shutil.copy(CANONICAL_PROFILES / "igor.yaml", d / "igor.yaml")
    return d


def _drain_events(server: IMAPServer) -> list:
    return server.fetch_unseen(INVALIDATE_MAILBOX)


# ── Profile diffing ──────────────────────────────────────────────────────────


def test_no_changes_no_invalidate(server, profiles_dir):
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server)
    published = inv.pump_once()
    assert published == 0
    assert _drain_events(server) == []


def test_profile_change_publishes_invalidate(server, profiles_dir):
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server)
    # Modify the YAML on disk → next pump should publish.
    igor_yaml = profiles_dir / "igor.yaml"
    igor_yaml.write_text(igor_yaml.read_text() + "\n# bumped\n")
    published = inv.pump_once()
    assert published == 1
    events = _drain_events(server)
    assert len(events) == 1
    assert events[0].payload["kind"] == "invalidate"
    assert events[0].payload["target"] == "igor"
    assert events[0].payload["reason"] == "changed"


def test_new_profile_added_publishes_invalidate(server, profiles_dir):
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server)
    shutil.copy(CANONICAL_PROFILES / "cc.yaml", profiles_dir / "cc.yaml")
    published = inv.pump_once()
    assert published == 1
    events = _drain_events(server)
    assert events[0].payload["target"] == "cc"
    assert events[0].payload["reason"] == "added"


def test_profile_deleted_publishes_invalidate(server, profiles_dir):
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server)
    (profiles_dir / "igor.yaml").unlink()
    published = inv.pump_once()
    assert published == 1
    events = _drain_events(server)
    assert events[0].payload["target"] == "igor"
    assert events[0].payload["reason"] == "removed"


def test_multiple_profile_changes_in_one_pump(server, profiles_dir):
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server)
    shutil.copy(CANONICAL_PROFILES / "cc.yaml", profiles_dir / "cc.yaml")
    shutil.copy(
        CANONICAL_PROFILES / "research-orca.yaml", profiles_dir / "research-orca.yaml"
    )
    igor_yaml = profiles_dir / "igor.yaml"
    igor_yaml.write_text(igor_yaml.read_text() + "\n# bumped\n")
    published = inv.pump_once()
    assert published == 3
    events = _drain_events(server)
    targets = {e.payload["target"] for e in events}
    assert targets == {"cc", "research-orca", "igor"}


# ── Registry diffing ─────────────────────────────────────────────────────────


class _MutableRegistry:
    def __init__(self, devices=None):
        self._devices = list(devices or [])

    def list_devices(self):
        return list(self._devices)

    def add(self, device_id, status="online"):
        self._devices.append({"device_id": device_id, "status": status})

    def set_status(self, device_id, status):
        for d in self._devices:
            if d.get("device_id") == device_id:
                d["status"] = status
                return


def test_registry_change_publishes_registry_invalidate(server, profiles_dir):
    reg = _MutableRegistry([{"device_id": "inference", "status": "online"}])
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server, registry=reg)
    reg.add("postgres", "online")
    published = inv.pump_once()
    assert published == 1
    events = _drain_events(server)
    assert events[0].payload["target"] == "registry"
    assert events[0].payload["reason"] == "changed"


def test_registry_status_flip_publishes_invalidate(server, profiles_dir):
    reg = _MutableRegistry([{"device_id": "inference", "status": "online"}])
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server, registry=reg)
    reg.set_status("inference", "offline")
    published = inv.pump_once()
    assert published == 1
    events = _drain_events(server)
    assert events[0].payload["target"] == "registry"


def test_registry_unchanged_no_invalidate(server, profiles_dir):
    reg = _MutableRegistry([{"device_id": "inference", "status": "online"}])
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server, registry=reg)
    published = inv.pump_once()
    assert published == 0
    assert _drain_events(server) == []


# ── Robustness ───────────────────────────────────────────────────────────────


def test_pump_handles_yaml_load_failure_gracefully(server, profiles_dir):
    """A profile being mid-write (partial YAML) shouldn't crash the loop."""
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server)
    # Replace igor.yaml with a binary blob that read_text will choke on isn't
    # easy — but unreadable files are handled by the WARNING + skip path.
    # Instead simulate a profile that vanishes between glob and read.
    bad = profiles_dir / "broken.yaml"
    bad.write_text("not: [unclosed")
    # pump_once should still see broken as added (etag computes fine on raw bytes)
    published = inv.pump_once()
    assert published == 1


def test_background_loop_stops_on_event(server, profiles_dir):
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server)
    inv.start(interval=0.05)
    # Let it spin a couple of cycles, then stop.
    time.sleep(0.2)
    inv.stop(timeout=0.5)
    # After stop, thread is None and we can call stop() again safely.
    inv.stop(timeout=0.1)


def test_background_loop_publishes_on_change(server, profiles_dir):
    """Smoke test that the background thread actually reaches pump_once."""
    inv = Invalidator(profiles_dir=profiles_dir, imap_server=server)
    inv.start(interval=0.05)
    try:
        # Make a change and wait for one or two poll cycles.
        igor_yaml = profiles_dir / "igor.yaml"
        igor_yaml.write_text(igor_yaml.read_text() + "\n# bg-bumped\n")
        # Poll for up to 1 second for the invalidate envelope to land.
        deadline = time.monotonic() + 1.0
        events: list = []
        while time.monotonic() < deadline:
            events = _drain_events(server)
            if events:
                break
            time.sleep(0.05)
        assert events, "background loop did not publish within 1s"
        assert events[0].payload["target"] == "igor"
    finally:
        inv.stop()

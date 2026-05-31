"""
Slice 4 — Igor.__init__ datacenter wiring tests.

Verifies _wire_datacenter():
  - Graceful fallback when no datacenter is reachable (datacenter_client = None)
  - Successful wire-up when a pre-constructed client is injected
  - Fallback still graceful when the injected client times out

Uses an Igor-shaped stub class rather than instantiating real Igor (whose
__init__ is 1000 lines long and would require a full SQLite + DB stack).
The _wire_datacenter method is invoked on the stub via the unbound function.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Test mode must be set BEFORE bus.imap_server is imported.
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

from unseen_university.announce import (
    DatacenterClient,
    IdentityEnvelope,
)
from unseen_university.skeleton.skeleton import Skeleton
from bus.imap_server import IMAPServer
from skeleton.registry import DeviceRegistry

CANONICAL_PROFILES = Path("/home/akien/dev/src/UnseenUniversity/config/profiles")


class _IgorShape:
    """Minimal Igor-shaped stub holding only what _wire_datacenter touches."""

    def __init__(self, instance_id="wild-0001", datacenter_client=None):
        self.instance_id = instance_id
        self.datacenter_client = datacenter_client
        self.datacenter_manifest = None
        # Slice 4b: _wire_datacenter now propagates dc_client onto cortex so
        # the engram executor can consult the manifest. Stub Cortex with a
        # plain object that accepts the assignment.
        from types import SimpleNamespace

        self.cortex = SimpleNamespace()


def _wire(stub):
    """Bind Igor._wire_datacenter to the stub instance and run it."""
    from devices.igor.main import Igor

    Igor._wire_datacenter(stub)


class TestIgorDatacenterBoot(unittest.TestCase):
    def test_wire_with_injected_client_caches_manifest(self):
        """When a working client is injected, manifest is cached."""
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            profiles_dir = tmp / "profiles"
            profiles_dir.mkdir()
            shutil.copy(CANONICAL_PROFILES / "igor.yaml", profiles_dir / "igor.yaml")

            server = IMAPServer()
            server.start()
            try:
                registry = DeviceRegistry(path=tmp / "devices.json")
                skel = Skeleton(
                    registry=registry,
                    imap_server=server,
                    profiles_dir=profiles_dir,
                )

                identity = IdentityEnvelope(
                    agent_id="igor",
                    instance="wild-0001",
                    box="testhost",
                    box_n=0,
                    pid=4242,
                    interface_version="1.0",
                    surfaces=["console", "inference"],
                )
                client = DatacenterClient(
                    agent_id=identity.agent_id,
                    instance=identity.instance,
                    box=identity.box,
                    box_n=identity.box_n,
                    pid=identity.pid,
                    surfaces=identity.surfaces,
                )
                stub = _IgorShape(instance_id="wild-0001", datacenter_client=client)

                # Background pump drives the listener while announce() polls.
                stop = threading.Event()

                def _pump():
                    while not stop.is_set():
                        skel.announce_pump()
                        time.sleep(0.02)

                t = threading.Thread(target=_pump, daemon=True)
                t.start()
                try:
                    _wire(stub)
                finally:
                    stop.set()
                    t.join(timeout=1.0)

                self.assertIsNotNone(stub.datacenter_manifest)
                self.assertEqual(
                    stub.datacenter_manifest["issued_to"]["agent_id"], "igor"
                )
            finally:
                server.stop()

    def test_wire_with_injected_client_handles_timeout(self):
        """Injected client whose announce() times out leaves manifest=None."""
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            server = IMAPServer()
            server.start()
            try:
                # No skeleton → no listener → announce() will time out.
                identity = IdentityEnvelope(
                    agent_id="igor",
                    instance="wild-0001",
                    box="testhost",
                    box_n=0,
                    pid=4242,
                    interface_version="1.0",
                )
                client = DatacenterClient(
                    agent_id=identity.agent_id,
                    instance=identity.instance,
                    box=identity.box,
                    box_n=identity.box_n,
                    pid=identity.pid,
                )
                # Override the default 2s timeout so the test runs fast.
                # We monkey-patch announce to use a 0.2s timeout instead.
                _orig = client.announce
                client.announce = lambda timeout=2.0, poll_interval=0.05: _orig(
                    timeout=0.2, poll_interval=0.05
                )

                stub = _IgorShape(instance_id="wild-0001", datacenter_client=client)
                _wire(stub)

                # Graceful fallback: manifest stays None, no exception raised.
                self.assertIsNone(stub.datacenter_manifest)
            finally:
                server.stop()

    def test_wire_default_path_no_datacenter_falls_back_silently(self):
        """No injected client + no real datacenter daemon → fallback, no crash."""
        # When datacenter_client is None, _wire_datacenter takes the default
        # connection path. With AGENT_DATACENTER_TEST_MODE=1 set above, IMAPServer
        # tries to spin up a stub on the test port. If another test is using it
        # we can still verify graceful fallback — the failure mode is "no
        # listener replies", which times out and falls back. We monkey-patch
        # IMAPServer().start() to raise so we exercise the import-failure path
        # rather than waiting 2 seconds for an announce timeout.
        from devices.igor import main as main_mod

        stub = _IgorShape(instance_id="wild-0001", datacenter_client=None)

        # Patch IMAPServer at the import site inside _wire_datacenter.
        # The simplest way is to inject a failing import via sys.modules.
        import sys

        original = sys.modules.get("bus.imap_server")
        try:

            class _FakeIMAP:
                def __init__(self, *a, **kw):
                    raise RuntimeError("simulated: no datacenter daemon")

                def start(self):
                    pass

            class _FakeMod:
                IMAPServer = _FakeIMAP

            sys.modules["bus.imap_server"] = _FakeMod  # type: ignore[assignment]
            _wire(stub)
        finally:
            if original is not None:
                sys.modules["bus.imap_server"] = original
            else:
                sys.modules.pop("bus.imap_server", None)

        # Graceful fallback: both attributes are None, no exception escaped.
        self.assertIsNone(stub.datacenter_client)
        self.assertIsNone(stub.datacenter_manifest)


if __name__ == "__main__":
    unittest.main()

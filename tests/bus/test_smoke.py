"""
Smoke test: IMAP bus end-to-end.

A message sent via Router.send() to a comms:// address wakes an IDLE
subscriber listening on the same mailbox.

Runs against the in-process IMAP stub (AGENT_DATACENTER_TEST_MODE=1).
"""

from __future__ import annotations

import os
import threading
import time

# Must be set before bus.imap_server is imported.
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

import pytest

from bus.envelope import Envelope
from bus.imap_server import IMAPServer
from unseen_university.bus.router import Router


@pytest.fixture()
def server():
    s = IMAPServer()
    s.start()
    yield s
    s.stop()


def test_comms_message_wakes_idle_subscriber(server):
    server.create_mailbox("akiendelllinux.0")
    router = Router(server)

    woke: list[bool] = []

    def subscriber():
        result = server.idle_wait("akiendelllinux.0", timeout_s=2.0)
        woke.append(result)

    t = threading.Thread(target=subscriber, daemon=True)
    t.start()
    time.sleep(0.02)  # let subscriber register the IDLE event

    env = Envelope.now(
        from_device="sender",
        to_device="akiendelllinux.0",
        payload={"hello": "world"},
    )
    router.send("comms://akiendelllinux.0", env)

    t.join(timeout=1.0)
    assert not t.is_alive(), "subscriber thread did not wake within 1s"
    assert woke == [True], "idle_wait must return True when woken by message arrival"

    messages = server.fetch_unseen("akiendelllinux.0")
    assert len(messages) == 1
    assert messages[0].payload["hello"] == "world"

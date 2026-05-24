"""
IMAP bus tests — mailboxes, routing, IDLE push, 24hr retention.

All tests run against the Python IMAP stub (AGENT_DATACENTER_TEST_MODE=1).
No system Dovecot or network IMAP server required.

The env var must be set before importing imap_server (it's read at module load).
This module sets it via conftest or monkeypatch at session scope, but since the
module-level _TEST_MODE flag in imap_server.py is set once at import, we ensure
the import happens after os.environ is set by placing the import inside a fixture.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone, timedelta

# Set test mode BEFORE any unseen_university bus imports
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

import pytest

from bus.envelope import Envelope
from bus.imap_server import IMAPServer, _STUB_MAILBOXES, _STUB_SEEN
from unseen_university.bus.router import AddressError, Router

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def server():
    """Fresh IMAPServer with clean stub state for each test."""
    s = IMAPServer()
    s.start()
    yield s
    s.stop()


def _envelope(from_dev="sender", to_dev="CC.0", **payload) -> Envelope:
    return Envelope.now(from_device=from_dev, to_device=to_dev, payload=payload)


# ── Mailbox lifecycle ─────────────────────────────────────────────────────────


def test_shared_mailbox_created_on_start(server):
    assert "Shared" in server.list_mailboxes()


def test_create_mailbox(server):
    server.create_mailbox("CC.0")
    assert "CC.0" in server.list_mailboxes()


def test_create_mailbox_idempotent(server):
    server.create_mailbox("CC.0")
    server.create_mailbox("CC.0")  # second call must not raise
    assert server.list_mailboxes().count("CC.0") == 1


def test_delete_mailbox(server):
    server.create_mailbox("CC.0")
    server.delete_mailbox("CC.0")
    assert "CC.0" not in server.list_mailboxes()


# ── Message routing via Router ────────────────────────────────────────────────


def test_router_send_direct(server):
    server.create_mailbox("CC.0")
    router = Router(server)
    env = _envelope(to_dev="CC.0")
    router.send("comms://CC.0", env)
    assert server.unseen_count("CC.0") == 1


def test_router_send_shared(server):
    router = Router(server)
    env = _envelope(to_dev="Shared")
    router.send("comms://Shared", env)
    assert server.unseen_count("Shared") == 1


def test_router_resolve_returns_mailbox_name(server):
    server.create_mailbox("igor-wild-0001")
    router = Router(server)
    assert router.resolve("comms://igor-wild-0001") == "igor-wild-0001"


def test_router_unknown_address_raises(server):
    router = Router(server)
    with pytest.raises(AddressError, match="nonexistent"):
        router.send("comms://nonexistent", _envelope())


def test_router_bad_scheme_raises(server):
    router = Router(server)
    with pytest.raises(AddressError, match="must start with"):
        router.resolve("smtp://CC.0")


# ── Suffix-style addressing (§ 14 decision 11.A) ──────────────────────────────


def test_router_resolve_peels_surface_suffix(server):
    """comms://mbox.surface peels to mbox when 'mbox' is a registered mailbox."""
    server.create_mailbox("igor-wild-0001")
    router = Router(server)
    assert router.resolve("comms://igor-wild-0001.console") == "igor-wild-0001"


def test_router_resolve_peels_multi_segment_surface(server):
    """comms://mbox.console.session-2 peels back to mbox."""
    server.create_mailbox("akiendelllinux.1")
    router = Router(server)
    assert (
        router.resolve("comms://akiendelllinux.1.console.session-2")
        == "akiendelllinux.1"
    )


def test_router_extract_surface_returns_qualifier(server):
    """extract_surface returns the dot-joined suffix after the matched mailbox."""
    server.create_mailbox("igor-wild-0001")
    router = Router(server)
    assert router.extract_surface("comms://igor-wild-0001.console") == "console"
    assert (
        router.extract_surface("comms://igor-wild-0001.console.tab-3")
        == "console.tab-3"
    )
    assert router.extract_surface("comms://igor-wild-0001") is None


def test_router_resolve_prefers_full_match_over_peel(server):
    """When the full path is itself a mailbox, the peel pass is not run."""
    server.create_mailbox("CC.0")
    server.create_mailbox("CC")
    router = Router(server)
    # CC.0 is itself a registered mailbox — must resolve to CC.0, not to CC.
    assert router.resolve("comms://CC.0") == "CC.0"
    assert router.extract_surface("comms://CC.0") is None


def test_router_resolve_suffix_no_prefix_match_raises(server):
    """No prefix registered → AddressError (no false-positive match on '')."""
    router = Router(server)
    with pytest.raises(AddressError, match="no prefix registered"):
        router.resolve("comms://nonexistent.foo.bar")


# ── Fetch + seen semantics ────────────────────────────────────────────────────


def test_fetch_unseen_returns_envelopes(server):
    server.create_mailbox("CC.0")
    env = _envelope(to_dev="CC.0", msg="hello")
    server.append("CC.0", env)
    fetched = server.fetch_unseen("CC.0")
    assert len(fetched) == 1
    assert fetched[0].payload.get("msg") == "hello"


def test_fetch_marks_seen(server):
    server.create_mailbox("CC.0")
    server.append("CC.0", _envelope(to_dev="CC.0"))
    server.fetch_unseen("CC.0")
    assert server.unseen_count("CC.0") == 0


def test_fetch_unseen_skips_already_seen(server):
    server.create_mailbox("CC.0")
    server.append("CC.0", _envelope(to_dev="CC.0", n=1))
    server.fetch_unseen("CC.0")  # marks seen
    server.append("CC.0", _envelope(to_dev="CC.0", n=2))
    fetched = server.fetch_unseen("CC.0")
    assert len(fetched) == 1
    assert fetched[0].payload.get("n") == 2


# ── IDLE push notification ────────────────────────────────────────────────────


def test_idle_push_wakes_within_timeout(server):
    """
    Appending a message to a mailbox while a client is IDLEing must wake the
    client within 2 seconds via the threading.Event mechanism in the stub.
    """
    server.create_mailbox("CC.0")

    received = threading.Event()

    def _listener():
        # Simulate an IDLE client: block until the event fires then check count
        import imaplib

        M = imaplib.IMAP4("127.0.0.1", server.port)
        M.login("user", "pass")
        M.select("CC.0")
        # Start IDLE
        M.send(b"A001 IDLE\r\n")
        M.readline()  # "+ idling"
        # Wait for EXISTS notification (sent when APPEND fires threading.Event)
        line = M.readline()
        if b"EXISTS" in line:
            received.set()
        M.send(b"A002 DONE\r\n")
        M.readline()
        M.logout()

    t = threading.Thread(target=_listener, daemon=True)
    t.start()
    time.sleep(0.1)  # let listener enter IDLE

    server.append("CC.0", _envelope(to_dev="CC.0"))
    assert received.wait(timeout=2.0), "IDLE client not woken within 2s"


# ── 24-hour retention ─────────────────────────────────────────────────────────


def test_purge_old_messages_removes_expired(server):
    server.create_mailbox("CC.0")
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    old_env = Envelope(
        from_device="sender",
        to_device="CC.0",
        sent_at=old_ts,
        payload={"age": "old"},
    )
    server.append("CC.0", old_env)
    assert server.unseen_count("CC.0") == 1

    purged = server.purge_old_messages(retention_hours=24)
    assert purged == 1
    assert server.unseen_count("CC.0") == 0


def test_purge_retains_recent_messages(server):
    server.create_mailbox("CC.0")
    recent_env = _envelope(to_dev="CC.0", age="recent")
    server.append("CC.0", recent_env)

    purged = server.purge_old_messages(retention_hours=24)
    assert purged == 0
    assert server.unseen_count("CC.0") == 1


def test_purge_retains_seen_within_24hr(server):
    """SEEN status does not trigger early deletion — expiry is time-based only."""
    server.create_mailbox("CC.0")
    env = _envelope(to_dev="CC.0")
    server.append("CC.0", env)
    server.fetch_unseen("CC.0")  # mark SEEN
    assert server.unseen_count("CC.0") == 0

    purged = server.purge_old_messages(retention_hours=24)
    assert purged == 0
    # Message still in mailbox (just SEEN, not expired)
    assert len(_STUB_MAILBOXES["CC.0"]) == 1


def test_purge_seen_expired_message(server):
    """SEEN message older than 24hr is purged just like UNSEEN."""
    server.create_mailbox("CC.0")
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    old_env = Envelope(
        from_device="sender",
        to_device="CC.0",
        sent_at=old_ts,
    )
    server.append("CC.0", old_env)
    server.fetch_unseen("CC.0")  # mark SEEN

    purged = server.purge_old_messages(retention_hours=24)
    assert purged == 1
    assert len(_STUB_MAILBOXES["CC.0"]) == 0


def test_purge_updates_seen_indices(server):
    """After purge, seen indices for retained messages must remain correct."""
    server.create_mailbox("CC.0")
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()

    # msg 0: old (will be purged)
    server.append(
        "CC.0",
        Envelope(from_device="s", to_device="CC.0", sent_at=old_ts),
    )
    # msg 1: recent, already SEEN
    recent_env = _envelope(to_dev="CC.0")
    server.append("CC.0", recent_env)
    server.fetch_unseen("CC.0")  # fetches both; marks both seen

    purged = server.purge_old_messages(retention_hours=24)
    assert purged == 1  # only the old one gone

    # Remaining message (was at index 1, now at index 0) should still be SEEN
    assert server.unseen_count("CC.0") == 0

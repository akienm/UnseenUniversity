"""
PgBus tests — Postgres-backed message bus.

Tests run against the real Postgres instance per unseenuniversity/rules/database
(no mocks). Each test gets a fresh bus fixture that cleans bus.mailboxes and
bus.messages before and after to ensure isolation.
"""

from __future__ import annotations

import os
import threading
import time

import psycopg2
import pytest

from bus.envelope import Envelope
from bus.pg_bus import PgBus, _channel
from unseen_university.bus.router import Router

_DSN = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

# ── Fixtures ───────────────────────────────────────────────────────────────────


def _clean_bus(dsn: str) -> None:
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bus.messages")
            cur.execute("DELETE FROM bus.mailboxes")


@pytest.fixture()
def bus():
    # Clean before start so start() creates a fresh Shared mailbox
    _clean_bus(_DSN)
    b = PgBus(dsn=_DSN)
    b.start()
    yield b
    _clean_bus(_DSN)
    b.stop()


def _env(from_dev: str = "sender", to_dev: str = "CC.0", **payload) -> Envelope:
    return Envelope.now(from_device=from_dev, to_device=to_dev, payload=payload)


# ── Mailbox lifecycle ──────────────────────────────────────────────────────────


def test_shared_mailbox_created_on_start(bus):
    assert "Shared" in bus.list_mailboxes()


def test_create_mailbox(bus):
    bus.create_mailbox("CC.0")
    assert "CC.0" in bus.list_mailboxes()


def test_create_mailbox_idempotent(bus):
    bus.create_mailbox("CC.0")
    bus.create_mailbox("CC.0")
    assert bus.list_mailboxes().count("CC.0") == 1


def test_delete_mailbox_removes_from_list(bus):
    bus.create_mailbox("CC.0")
    bus.delete_mailbox("CC.0")
    assert "CC.0" not in bus.list_mailboxes()


def test_delete_mailbox_removes_messages(bus):
    bus.create_mailbox("CC.0")
    bus.append("CC.0", _env(to_dev="CC.0"))
    bus.delete_mailbox("CC.0")
    # Direct DB check — messages table should be empty for this mailbox
    with psycopg2.connect(_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM bus.messages WHERE mailbox = 'CC.0'")
            assert cur.fetchone()[0] == 0


# ── Append + fetch semantics ───────────────────────────────────────────────────


def test_append_and_fetch_unseen(bus):
    bus.create_mailbox("CC.0")
    env = _env(to_dev="CC.0", msg="hello")
    bus.append("CC.0", env)
    fetched = bus.fetch_unseen("CC.0")
    assert len(fetched) == 1
    assert fetched[0].payload.get("msg") == "hello"


def test_fetch_unseen_marks_seen(bus):
    bus.create_mailbox("CC.0")
    bus.append("CC.0", _env(to_dev="CC.0"))
    bus.fetch_unseen("CC.0")
    assert bus.unseen_count("CC.0") == 0


def test_fetch_unseen_skips_already_seen(bus):
    bus.create_mailbox("CC.0")
    bus.append("CC.0", _env(to_dev="CC.0", n=1))
    bus.fetch_unseen("CC.0")
    bus.append("CC.0", _env(to_dev="CC.0", n=2))
    fetched = bus.fetch_unseen("CC.0")
    assert len(fetched) == 1
    assert fetched[0].payload.get("n") == 2


def test_unseen_count(bus):
    bus.create_mailbox("CC.0")
    assert bus.unseen_count("CC.0") == 0
    bus.append("CC.0", _env(to_dev="CC.0"))
    bus.append("CC.0", _env(to_dev="CC.0"))
    assert bus.unseen_count("CC.0") == 2


def test_fetch_recent_does_not_mark_seen(bus):
    bus.create_mailbox("CC.0")
    bus.append("CC.0", _env(to_dev="CC.0"))
    bus.fetch_recent("CC.0")
    assert bus.unseen_count("CC.0") == 1


def test_fetch_recent_respects_limit(bus):
    bus.create_mailbox("CC.0")
    for i in range(5):
        bus.append("CC.0", _env(to_dev="CC.0", n=i))
    recent = bus.fetch_recent("CC.0", limit=3)
    assert len(recent) == 3


def test_fetch_recent_returns_chronological_order(bus):
    bus.create_mailbox("CC.0")
    for i in range(3):
        bus.append("CC.0", _env(to_dev="CC.0", n=i))
    recent = bus.fetch_recent("CC.0", limit=3)
    assert [e.payload["n"] for e in recent] == [0, 1, 2]


# ── Router integration ─────────────────────────────────────────────────────────


def test_router_send_direct(bus):
    bus.create_mailbox("CC.0")
    router = Router(bus)
    router.send("comms://CC.0", _env(to_dev="CC.0"))
    assert bus.unseen_count("CC.0") == 1


def test_router_unknown_address_raises(bus):
    from unseen_university.bus.router import AddressError

    router = Router(bus)
    with pytest.raises(AddressError, match="nonexistent"):
        router.send("comms://nonexistent", _env())


# ── IDLE push notification ─────────────────────────────────────────────────────


def test_idle_wait_wakes_on_append(bus):
    bus.create_mailbox("CC.0")
    woke: list[bool] = []

    def _listener():
        woke.append(bus.idle_wait("CC.0", timeout_s=3.0))

    t = threading.Thread(target=_listener, daemon=True)
    t.start()
    time.sleep(0.15)  # let listener establish LISTEN connection

    bus.append("CC.0", _env(to_dev="CC.0"))
    t.join(timeout=2.0)
    assert not t.is_alive(), "idle_wait did not wake within 2s"
    assert woke == [True]


def test_idle_wait_returns_false_on_timeout(bus):
    bus.create_mailbox("CC.0")
    result = bus.idle_wait("CC.0", timeout_s=0.2)
    assert result is False


def test_idle_wait_fast_path_when_messages_pending(bus):
    bus.create_mailbox("CC.0")
    bus.append("CC.0", _env(to_dev="CC.0"))
    result = bus.idle_wait("CC.0", timeout_s=0.0)
    assert result is True


# ── Purge old messages ─────────────────────────────────────────────────────────


def test_purge_removes_old_rows(bus):
    bus.create_mailbox("CC.0")
    # Insert a row with an old created_at directly
    with psycopg2.connect(_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bus.messages (mailbox, from_device, envelope_json, created_at)"
                " VALUES ('CC.0', 'sender', %s, now() - interval '25 hours')",
                (_env(to_dev="CC.0").to_json(),),
            )
    assert bus.unseen_count("CC.0") == 1
    purged = bus.purge_old_messages(retention_hours=24)
    assert purged == 1
    assert bus.unseen_count("CC.0") == 0


def test_purge_retains_recent_messages(bus):
    bus.create_mailbox("CC.0")
    bus.append("CC.0", _env(to_dev="CC.0"))
    purged = bus.purge_old_messages(retention_hours=24)
    assert purged == 0
    assert bus.unseen_count("CC.0") == 1


# ── Channel name sanitizer ─────────────────────────────────────────────────────


def test_channel_sanitizes_dots_and_hyphens():
    assert _channel("CC.0") == "cc_0"
    assert _channel("dicksimnel.0") == "dicksimnel_0"
    assert _channel("igor-wild-0001") == "igor_wild_0001"

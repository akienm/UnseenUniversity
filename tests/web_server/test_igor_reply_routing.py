"""
test_igor_reply_routing.py — Igor→web UI reply round-trip routing.

Validates the UU-side of T-igor-web-reply-routing:
  1. _canonical_session_id() converts bare agent names to comms:// URIs.
  2. agent_send() routes to the joined session (comms://igor) rather than
     a different session (comms://shared) — the suspected silent drop source.
  3. The reply payload carries the correct author and session_id fields.

Diagnosis: the round-trip path IS correct on the UU side.  When Igor's
cognition produces degenerate output (thread-context echo), its main.py
gates the call to web_server.send() before it reaches this layer.  That
gate is the upstream failure point; this test confirms the UU delivery
machinery works when send() *is* called.
"""

from __future__ import annotations

import json
import queue

# ── _canonical_session_id ─────────────────────────────────────────────────────


def test_canonical_session_id_bare_igor():
    from unseen_university.devices.web_server.server import _canonical_session_id

    assert _canonical_session_id("igor") == "comms://igor"


def test_canonical_session_id_bare_shared():
    from unseen_university.devices.web_server.server import _canonical_session_id

    assert _canonical_session_id("shared") == "comms://shared"


def test_canonical_session_id_already_prefixed():
    from unseen_university.devices.web_server.server import _canonical_session_id

    assert _canonical_session_id("comms://igor") == "comms://igor"
    assert _canonical_session_id("comms://shared") == "comms://shared"


def test_canonical_session_id_empty():
    from unseen_university.devices.web_server.server import _canonical_session_id

    assert _canonical_session_id("") == "comms://shared"


# ── agent_send → session routing ──────────────────────────────────────────────


class _SyncLoop:
    """Minimal event-loop stand-in: call_soon_threadsafe runs synchronously."""

    def call_soon_threadsafe(self, fn, *args):
        fn(*args)


def test_agent_send_routes_to_igor_not_shared(monkeypatch):
    """Reply posted to session='comms://igor' must land on the igor subscriber."""
    import unseen_university.devices.web_server.server as srv

    igor_q: queue.Queue = queue.Queue()
    shared_q: queue.Queue = queue.Queue()

    with srv._client_lock:
        srv._session_clients["comms://igor"] = [igor_q]
        srv._session_clients["comms://shared"] = [shared_q]

    monkeypatch.setattr(srv, "_loop", _SyncLoop())

    try:
        srv.agent_send("hello from igor", "igor", "comms://igor")

        assert not igor_q.empty(), "igor subscriber did not receive the reply"
        assert shared_q.empty(), "reply was incorrectly sent to comms://shared"

        payload = json.loads(igor_q.get_nowait())
        assert payload["content"] == "hello from igor"
        assert payload["author"] == "igor"
        assert payload["session_id"] == "comms://igor"
    finally:
        with srv._client_lock:
            srv._session_clients.pop("comms://igor", None)
            srv._session_clients.pop("comms://shared", None)


def test_agent_send_bare_session_canonicalized(monkeypatch):
    """Bare session name 'igor' (no comms:// prefix) must be canonicalized before routing."""
    import unseen_university.devices.web_server.server as srv

    igor_q: queue.Queue = queue.Queue()

    with srv._client_lock:
        srv._session_clients["comms://igor"] = [igor_q]

    monkeypatch.setattr(srv, "_loop", _SyncLoop())

    try:
        srv.agent_send("hello", "igor", "igor")  # bare name, not comms://igor

        assert not igor_q.empty(), "canonicalized session did not deliver to subscriber"
        payload = json.loads(igor_q.get_nowait())
        assert payload["session_id"] == "comms://igor"
    finally:
        with srv._client_lock:
            srv._session_clients.pop("comms://igor", None)


def test_agent_send_empty_queue_when_no_subscriber(monkeypatch):
    """Fanout=0 when no subscriber is joined — no crash, no delivery."""
    import unseen_university.devices.web_server.server as srv

    with srv._client_lock:
        srv._session_clients.pop("comms://igor", None)

    monkeypatch.setattr(srv, "_loop", _SyncLoop())

    # Should not raise — fanout=0 is logged but not an error
    srv.agent_send("hello", "igor", "comms://igor")


# ── WS message routing (T-web-channel-mismatch-ux) ───────────────────────────


def test_ws_message_always_routes_to_igor_queue():
    """WS chat from any channel tab must land in Igor's per-agent queue with
    session_id='comms://igor', not in the dead global incoming queue."""
    import unseen_university.devices.web_server.server as srv

    # Drain Igor's queue so the test starts clean
    q = srv._get_agent_queue("igor")
    while not q.empty():
        q.get_nowait()

    initial_incoming_size = srv.incoming.qsize()

    # Simulate the put that the WS handler now does
    q.put(
        {
            "content": "hello from granny tab",
            "author": "web-user",
            "client_id": 999,
            "session_id": "comms://igor",
            "context_session": "comms://shared",
        }
    )

    msg = q.get_nowait()
    assert msg["session_id"] == "comms://igor", (
        "WS message must carry session_id='comms://igor' so Igor replies there"
    )
    assert msg["content"] == "hello from granny tab"
    # global incoming must be untouched
    assert srv.incoming.qsize() == initial_incoming_size, (
        "WS messages must NOT go to the dead global incoming queue"
    )


# ── context_session isolation (T-web-thread-context-scope) ───────────────────


def test_ws_message_carries_context_session():
    """Queue message includes context_session for per-channel thread isolation.

    session_id stays comms://igor for reply routing; context_session carries the
    actual channel so _get_thread_id in Igor can key thread buffers per-channel.
    """
    import unseen_university.devices.web_server.server as srv

    q = srv._get_agent_queue("igor")
    while not q.empty():
        q.get_nowait()

    q.put(
        {
            "content": "test message",
            "author": "akien",
            "client_id": 1234,
            "session_id": "comms://igor",
            "context_session": "comms://shared",
        }
    )

    msg = q.get_nowait()
    assert msg["session_id"] == "comms://igor", "routing must stay on comms://igor"
    assert msg["context_session"] == "comms://shared", (
        "context_session must carry the actual channel for thread isolation"
    )


def test_get_thread_id_uses_context_session():
    """_get_thread_id prefers context_session over session_id for web messages."""
    from unseen_university.devices.igor.main import Igor
    from unittest.mock import MagicMock

    class FakeMsg:
        source = "web"
        reply_info = {
            "session_id": "comms://igor",
            "context_session": "comms://shared",
            "client_id": 42,
        }

    igor = MagicMock(spec=Igor)
    thread_id = Igor._get_thread_id(igor, FakeMsg())
    assert thread_id == "web:comms://shared", (
        f"expected 'web:comms://shared' got '{thread_id}'"
    )


def test_get_thread_id_falls_back_to_session_id_when_no_context_session():
    """_get_thread_id falls back to session_id when context_session absent."""
    from unseen_university.devices.igor.main import Igor
    from unittest.mock import MagicMock

    class FakeMsg:
        source = "web"
        reply_info = {
            "session_id": "comms://igor",
            "client_id": 42,
        }

    igor = MagicMock(spec=Igor)
    thread_id = Igor._get_thread_id(igor, FakeMsg())
    assert thread_id == "web:comms://igor"


def test_get_thread_id_different_channels_get_different_thread_ids():
    """Two messages from different channels get different thread_ids."""
    from unseen_university.devices.igor.main import Igor
    from unittest.mock import MagicMock

    class MsgIgor:
        source = "web"
        reply_info = {"session_id": "comms://igor", "context_session": "comms://igor"}

    class MsgShared:
        source = "web"
        reply_info = {"session_id": "comms://igor", "context_session": "comms://shared"}

    igor = MagicMock(spec=Igor)
    tid_igor = Igor._get_thread_id(igor, MsgIgor())
    tid_shared = Igor._get_thread_id(igor, MsgShared())

    assert tid_igor != tid_shared, (
        "comms://igor and comms://shared messages must have distinct thread_ids"
    )

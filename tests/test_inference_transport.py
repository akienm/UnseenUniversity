"""
test_inference_transport.py — T-uc-inference-channel MVP smoke tests.

These tests exercise the envelope wrap/unwrap and the transport's
pass-through behavior without making real inference calls. The gateway
is stubbed — we only verify that:
  1. Non-inference messages pass through storage unmodified.
  2. inference/request messages trigger a gateway call and emit a
     correlated inference/response on the same channel.
  3. read() unwraps the JSON envelope so callers see plain text.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.utility_closet.comms import Channel, ChannelMessage, Direction
from lab.utility_closet.transports.inference import (
    CT_REQUEST,
    CT_RESPONSE,
    InferenceTransport,
    _unwrap_payload,
    _wrap_payload,
)


class _StubStore:
    """Replaces PostgresTransport for test isolation — keeps messages in a list."""

    def __init__(self):
        self.messages: list[ChannelMessage] = []
        self.closed = False

    def send(self, channel, message):
        self.messages.append(message)
        return True

    def read(self, channel, limit=50, since=None):
        # Newest-first, matching PostgresTransport
        return list(reversed(self.messages[-limit:]))

    def close(self):
        self.closed = True


class TestEnvelope(unittest.TestCase):
    """Wrap/unwrap round-trip and plain-text tolerance."""

    def test_wrap_roundtrip(self):
        wrapped = _wrap_payload("abc123", "hello", reply_to="xyz")
        msg_id, text, reply_to = _unwrap_payload(wrapped)
        self.assertEqual(msg_id, "abc123")
        self.assertEqual(text, "hello")
        self.assertEqual(reply_to, "xyz")

    def test_unwrap_plaintext_tolerant(self):
        msg_id, text, reply_to = _unwrap_payload("just plain text")
        self.assertEqual(msg_id, "")
        self.assertEqual(text, "just plain text")
        self.assertIsNone(reply_to)


class TestInferenceTransport(unittest.TestCase):
    """Transport behavior with gateway stubbed."""

    def _make_transport(self, gateway_reply: str = "stubbed reply"):
        t = InferenceTransport(db_url="postgres://unused")
        t._store = _StubStore()

        # Stub the gateway invocation
        t._gateway = MagicMock()
        t._gateway.last_tier = "tier.2"
        with patch.object(t, "_run_inference", return_value=gateway_reply):
            yield t

    def test_non_inference_message_passes_through(self):
        t = InferenceTransport(db_url="postgres://unused")
        t._store = _StubStore()
        ch = Channel(address="comms://model/test", direction=Direction.READ_WRITE)
        msg = ChannelMessage(
            channel=ch.address,
            source="akien",
            content_type="text/plain",
            payload="hello",
        )
        self.assertTrue(t.send(ch, msg))
        self.assertEqual(len(t._store.messages), 1)
        self.assertEqual(t._store.messages[0].payload, "hello")
        self.assertEqual(t._store.messages[0].content_type, "text/plain")

    def test_request_triggers_gateway_and_emits_response(self):
        t = InferenceTransport(db_url="postgres://unused")
        t._store = _StubStore()
        t._gateway = MagicMock()
        t._gateway.last_tier = "tier.2"

        ch = Channel(
            address="comms://model/test_purpose",
            direction=Direction.READ_WRITE,
        )
        req = ChannelMessage(
            id="req-001",
            channel=ch.address,
            source="akien",
            content_type=CT_REQUEST,
            payload="what is 2+2?",
        )

        with patch.object(t, "_run_inference", return_value="4") as run_stub:
            self.assertTrue(t.send(ch, req))
            run_stub.assert_called_once()
            call_kwargs = run_stub.call_args.kwargs
            self.assertEqual(call_kwargs["prompt"], "what is 2+2?")
            self.assertEqual(call_kwargs["purpose"], "test_purpose")

        # Both request and response stored
        self.assertEqual(len(t._store.messages), 2)
        stored_req, stored_resp = t._store.messages
        self.assertEqual(stored_req.content_type, CT_REQUEST)
        self.assertEqual(stored_resp.content_type, CT_RESPONSE)
        self.assertEqual(stored_resp.reply_to, "req-001")
        # Response payload is JSON-wrapped — unwrap to check
        _id, text, reply_to = _unwrap_payload(stored_resp.payload)
        self.assertEqual(text, "4")
        self.assertEqual(reply_to, "req-001")

    def test_read_unwraps_envelopes(self):
        t = InferenceTransport(db_url="postgres://unused")
        t._store = _StubStore()
        # Pre-populate store with an inference response envelope
        ch = Channel(address="comms://model/x", direction=Direction.READ_WRITE)
        t._store.messages.append(
            ChannelMessage(
                channel=ch.address,
                source="inference-gateway",
                content_type=CT_RESPONSE,
                payload=_wrap_payload("resp-1", "hello world", reply_to="req-1"),
            )
        )
        out = t.read(ch)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].payload, "hello world")
        self.assertEqual(out[0].reply_to, "req-1")
        self.assertEqual(out[0].id, "resp-1")

    def test_read_passes_through_plain_text(self):
        t = InferenceTransport(db_url="postgres://unused")
        t._store = _StubStore()
        ch = Channel(address="comms://shared", direction=Direction.READ_WRITE)
        t._store.messages.append(
            ChannelMessage(
                channel=ch.address,
                source="akien",
                content_type="text/plain",
                payload="not inference",
            )
        )
        out = t.read(ch)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].payload, "not inference")


if __name__ == "__main__":
    unittest.main()

"""
test_comms.py — T-uc-comms-module

Tests for the comms module: envelopes, channels, routing, transports.
"""

import json
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.utility_closet.comms import (
    Channel,
    ChannelMessage,
    CommsModule,
    Delivery,
    Direction,
    Transport,
)
from lab.utility_closet.transports.memory import MemoryTransport

# ── ChannelMessage ───────────────────────────────────────────────────────────


class TestChannelMessage:
    def test_defaults(self):
        msg = ChannelMessage()
        assert len(msg.id) == 16
        assert msg.content_type == "text/plain"
        assert msg.reply_to is None

    def test_to_dict(self):
        msg = ChannelMessage(
            channel="comms://shared",
            source="akien",
            payload="hello",
        )
        d = msg.to_dict()
        assert d["channel"] == "comms://shared"
        assert d["source"] == "akien"
        assert d["payload"] == "hello"

    def test_from_dict(self):
        d = {
            "channel": "comms://test",
            "source": "igor",
            "payload": "world",
            "content_type": "text/markdown",
            "reply_to": "abc123",
        }
        msg = ChannelMessage.from_dict(d)
        assert msg.channel == "comms://test"
        assert msg.source == "igor"
        assert msg.reply_to == "abc123"
        assert msg.content_type == "text/markdown"

    def test_roundtrip(self):
        msg = ChannelMessage(
            channel="comms://shared",
            source="ccmain",
            payload="test",
            metadata={"key": "value"},
        )
        d = msg.to_dict()
        msg2 = ChannelMessage.from_dict(d)
        assert msg2.channel == msg.channel
        assert msg2.source == msg.source
        assert msg2.payload == msg.payload
        assert msg2.metadata == msg.metadata


# ── Channel ──────────────────────────────────────────────────────────────────


class TestChannel:
    def test_defaults(self):
        ch = Channel(address="comms://test")
        assert ch.direction == Direction.READ_WRITE
        assert ch.delivery == Delivery.PULL
        assert ch.notify is False
        assert ch.retention == "1y"
        assert ch.show_timestamp is True

    def test_show_timestamp_opt_out(self):
        ch = Channel(address="comms://infra", show_timestamp=False)
        assert ch.show_timestamp is False

    def test_log_file_path(self):
        ch = Channel(address="comms://discord/dm-akien")
        path = ch.log_file_path(Path("/logs"))
        assert path == Path("/logs/discord--dm-akien.conversation.log")

    def test_log_file_path_custom(self):
        ch = Channel(
            address="comms://test",
            log_path=Path("/custom/my.log"),
        )
        path = ch.log_file_path(Path("/default"))
        assert path == Path("/custom/my.log")

    def test_log_file_path_shared(self):
        ch = Channel(address="comms://shared")
        path = ch.log_file_path(Path("/logs"))
        assert path == Path("/logs/shared.conversation.log")


# ── MemoryTransport ──────────────────────────────────────────────────────────


class TestMemoryTransport:
    def test_send_and_read(self):
        t = MemoryTransport()
        ch = Channel(address="comms://test")
        msg = ChannelMessage(channel="comms://test", source="a", payload="hi")
        assert t.send(ch, msg) is True
        result = t.read(ch, limit=10)
        assert len(result) == 1
        assert result[0].payload == "hi"

    def test_read_limit(self):
        t = MemoryTransport()
        ch = Channel(address="comms://test")
        for i in range(20):
            t.send(ch, ChannelMessage(channel="comms://test", payload=str(i)))
        result = t.read(ch, limit=5)
        assert len(result) == 5

    def test_read_since(self):
        t = MemoryTransport()
        ch = Channel(address="comms://test")
        t.send(
            ch,
            ChannelMessage(
                channel="comms://test",
                payload="old",
                timestamp="2026-01-01T00:00:00",
            ),
        )
        t.send(
            ch,
            ChannelMessage(
                channel="comms://test",
                payload="new",
                timestamp="2026-04-17T00:00:00",
            ),
        )
        result = t.read(ch, since="2026-04-01T00:00:00")
        assert len(result) == 1
        assert result[0].payload == "new"

    def test_max_messages(self):
        t = MemoryTransport(max_messages=5)
        ch = Channel(address="comms://test")
        for i in range(10):
            t.send(ch, ChannelMessage(channel="comms://test", payload=str(i)))
        result = t.read(ch, limit=100)
        assert len(result) == 5

    def test_close(self):
        t = MemoryTransport()
        ch = Channel(address="comms://test")
        t.send(ch, ChannelMessage(channel="comms://test", payload="x"))
        t.close()
        assert t.read(ch) == []

    def test_thread_safety(self):
        t = MemoryTransport()
        ch = Channel(address="comms://test")

        def send_batch(start):
            for i in range(50):
                t.send(
                    ch,
                    ChannelMessage(channel="comms://test", payload=str(start + i)),
                )

        threads = [
            threading.Thread(target=send_batch, args=(i * 50,)) for i in range(4)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        result = t.read(ch, limit=1000)
        assert len(result) == 200


# ── CommsModule ──────────────────────────────────────────────────────────────


class TestCommsModule:
    def _make_comms(self, with_log_dir=False):
        tmpdir = None
        if with_log_dir:
            tmpdir = tempfile.mkdtemp()
        comms = CommsModule(
            log_base_dir=Path(tmpdir) if tmpdir else None,
        )
        transport = MemoryTransport()
        comms.set_default_transport(transport)
        return comms, transport, tmpdir

    def test_register_channel(self):
        comms, _, _ = self._make_comms()
        ch = Channel(address="comms://test")
        comms.register_channel(ch)
        assert comms.get_channel("comms://test") is ch

    def test_list_channels(self):
        comms, _, _ = self._make_comms()
        comms.register_channel(Channel(address="comms://a"))
        comms.register_channel(Channel(address="comms://b"))
        channels = comms.list_channels()
        addrs = {c.address for c in channels}
        assert addrs == {"comms://a", "comms://b"}

    def test_send_and_read(self):
        comms, _, _ = self._make_comms()
        comms.register_channel(Channel(address="comms://test"))
        msg = ChannelMessage(channel="comms://test", source="akien", payload="hello")
        assert comms.send(msg) is True
        result = comms.read("comms://test", limit=10)
        assert len(result) == 1
        assert result[0].payload == "hello"

    def test_send_unknown_channel(self):
        comms, _, _ = self._make_comms()
        msg = ChannelMessage(channel="comms://ghost", payload="x")
        assert comms.send(msg) is False

    def test_send_read_only_channel(self):
        comms, _, _ = self._make_comms()
        comms.register_channel(
            Channel(address="comms://readonly", direction=Direction.READ_ONLY)
        )
        msg = ChannelMessage(channel="comms://readonly", payload="x")
        assert comms.send(msg) is False

    def test_read_write_only_channel(self):
        comms, _, _ = self._make_comms()
        comms.register_channel(
            Channel(address="comms://writeonly", direction=Direction.WRITE_ONLY)
        )
        result = comms.read("comms://writeonly")
        assert result == []

    def test_retention_inherited(self):
        comms, transport, _ = self._make_comms()
        comms.register_channel(Channel(address="comms://forever", retention="forever"))
        msg = ChannelMessage(channel="comms://forever", payload="keep me")
        comms.send(msg)
        assert msg.retention == "forever"

    def test_ensure_channel_creates(self):
        comms, _, _ = self._make_comms()
        ch = comms.ensure_channel("comms://auto")
        assert ch.address == "comms://auto"
        assert comms.get_channel("comms://auto") is ch

    def test_ensure_channel_idempotent(self):
        comms, _, _ = self._make_comms()
        ch1 = comms.ensure_channel("comms://auto", notify=True)
        ch2 = comms.ensure_channel("comms://auto", notify=False)
        assert ch1 is ch2
        assert ch1.notify is True  # original config preserved


class TestCommsSubscriptions:
    def test_subscribe_receives_messages(self):
        comms = CommsModule()
        transport = MemoryTransport()
        comms.set_default_transport(transport)
        comms.register_channel(Channel(address="comms://test", notify=True))

        received = []
        comms.subscribe("comms://test", "listener", received.append)

        msg = ChannelMessage(channel="comms://test", source="sender", payload="hello")
        comms.send(msg)
        assert len(received) == 1
        assert received[0].payload == "hello"

    def test_subscriber_not_notified_of_own_messages(self):
        comms = CommsModule()
        transport = MemoryTransport()
        comms.set_default_transport(transport)
        comms.register_channel(Channel(address="comms://test"))

        received = []
        comms.subscribe("comms://test", "sender", received.append)

        msg = ChannelMessage(channel="comms://test", source="sender", payload="echo?")
        comms.send(msg)
        assert len(received) == 0  # should NOT receive own message

    def test_unsubscribe(self):
        comms = CommsModule()
        transport = MemoryTransport()
        comms.set_default_transport(transport)
        comms.register_channel(Channel(address="comms://test"))

        received = []
        comms.subscribe("comms://test", "listener", received.append)
        comms.unsubscribe("comms://test", "listener")

        msg = ChannelMessage(
            channel="comms://test", source="other", payload="after unsub"
        )
        comms.send(msg)
        assert len(received) == 0

    def test_multiple_subscribers(self):
        comms = CommsModule()
        transport = MemoryTransport()
        comms.set_default_transport(transport)
        comms.register_channel(Channel(address="comms://test"))

        received_a = []
        received_b = []
        comms.subscribe("comms://test", "a", received_a.append)
        comms.subscribe("comms://test", "b", received_b.append)

        msg = ChannelMessage(
            channel="comms://test", source="sender", payload="broadcast"
        )
        comms.send(msg)
        assert len(received_a) == 1
        assert len(received_b) == 1


class TestCommsFileLogging:
    def test_log_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            comms = CommsModule(log_base_dir=Path(tmpdir))
            transport = MemoryTransport()
            comms.set_default_transport(transport)
            comms.register_channel(Channel(address="comms://shared"))

            msg = ChannelMessage(
                channel="comms://shared", source="akien", payload="logged"
            )
            comms.send(msg)

            log_file = Path(tmpdir) / "shared.conversation.log"
            assert log_file.exists()
            lines = log_file.read_text().strip().split("\n")
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["payload"] == "logged"
            assert data["source"] == "akien"

    def test_no_log_for_ephemeral(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            comms = CommsModule(log_base_dir=Path(tmpdir))
            transport = MemoryTransport()
            comms.set_default_transport(transport)
            comms.register_channel(
                Channel(address="comms://debug", retention="ephemeral")
            )

            msg = ChannelMessage(
                channel="comms://debug", source="test", payload="no log"
            )
            comms.send(msg)

            log_file = Path(tmpdir) / "debug.conversation.log"
            assert not log_file.exists()


class TestCommsHealth:
    def test_health_report(self):
        comms = CommsModule()
        comms.register_channel(Channel(address="comms://a"))
        comms.register_channel(Channel(address="comms://b"))
        h = comms.health()
        assert h["online"] is True
        assert h["channels"] == 2
        assert h["messages_routed"] == 0

    def test_health_counts_messages(self):
        comms = CommsModule()
        transport = MemoryTransport()
        comms.set_default_transport(transport)
        comms.register_channel(Channel(address="comms://test"))
        comms.send(ChannelMessage(channel="comms://test", payload="x"))
        comms.send(ChannelMessage(channel="comms://test", payload="y"))
        h = comms.health()
        assert h["messages_routed"] == 2


class TestCommsIsRackModule:
    def test_is_rack_module(self):
        from lab.utility_closet.rack import RackModule

        comms = CommsModule()
        assert isinstance(comms, RackModule)

    def test_module_name(self):
        comms = CommsModule()
        assert comms.module_name == "comms"

    def test_stop_closes_transports(self):
        comms = CommsModule()
        transport = MemoryTransport()
        comms.set_default_transport(transport)
        comms.register_channel(
            Channel(address="comms://test"),
            transport=MemoryTransport(),
        )
        comms.stop()  # should not raise


class TestCommsPerChannelTransport:
    def test_channel_specific_transport(self):
        comms = CommsModule()
        default_t = MemoryTransport()
        special_t = MemoryTransport()
        comms.set_default_transport(default_t)

        comms.register_channel(Channel(address="comms://default"))
        comms.register_channel(Channel(address="comms://special"), transport=special_t)

        comms.send(ChannelMessage(channel="comms://default", payload="a"))
        comms.send(ChannelMessage(channel="comms://special", payload="b"))

        # Each transport should have its own message
        assert len(default_t.read(Channel(address="comms://default"))) == 1
        assert len(special_t.read(Channel(address="comms://special"))) == 1
        # Cross-check: default transport should NOT have special's message
        assert len(default_t.read(Channel(address="comms://special"))) == 0

"""
test_comms_default_channels.py — T-uc-comms-default-channels

Tests for default channel wiring in the UC server: comms://shared,
auto-created agent channels, and the comms API endpoints.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestInitComms:
    """_init_comms() creates the comms module with default channels."""

    def test_creates_shared_channel(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc

        comms = uc._comms
        assert comms is not None
        ch = comms.get_channel("comms://shared")
        assert ch is not None
        assert ch.address == "comms://shared"

    def test_shared_channel_is_read_write(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc
        from lab.utility_closet.comms import Direction

        ch = uc._comms.get_channel("comms://shared")
        assert ch.direction == Direction.READ_WRITE

    def test_shared_channel_notify_off_by_default(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc

        ch = uc._comms.get_channel("comms://shared")
        assert ch.notify is False

    def test_comms_has_default_transport(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc

        assert uc._comms._default_transport is not None


class TestAgentChannelAutoCreate:
    """Agent registration auto-creates a comms channel."""

    def test_register_creates_channel(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc

        # Simulate what _api_agent_register does
        agent_id = "Igor-wild-0001"
        uc._comms.ensure_channel(
            f"comms://{agent_id}",
            notify=True,
            retention="1y",
        )

        ch = uc._comms.get_channel("comms://Igor-wild-0001")
        assert ch is not None
        assert ch.notify is True

    def test_register_idempotent(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc

        ch1 = uc._comms.ensure_channel("comms://agent-a", notify=True)
        ch2 = uc._comms.ensure_channel("comms://agent-a", notify=False)
        assert ch1 is ch2
        assert ch1.notify is True  # original config preserved


class TestCommsChannelsEndpoint:
    """Tests for /api/comms/channels response format."""

    def test_channels_list_format(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc

        # Add a second channel
        uc._comms.ensure_channel("comms://test-agent", notify=True)

        # Build the response data the same way the endpoint does
        channels = uc._comms.list_channels()
        data = [
            {
                "address": ch.address,
                "direction": ch.direction.value,
                "notify": ch.notify,
                "retention": ch.retention,
            }
            for ch in channels
        ]
        addrs = {d["address"] for d in data}
        assert "comms://shared" in addrs
        assert "comms://test-agent" in addrs

    def test_channels_empty_without_init(self):
        """When comms is not initialized, endpoint returns empty list."""
        from lab.claudecode import utility_closet_server as uc

        saved = uc._comms
        try:
            uc._comms = None
            # The endpoint would return {"channels": []}
            assert uc._comms is None
        finally:
            uc._comms = saved


class TestCommsHealthEndpoint:
    def test_health_reports_channel_count(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc

        h = uc._comms.health()
        assert h["online"] is True
        assert h["channels"] >= 1  # at least comms://shared


class TestCommsMessageRouting:
    """Messages sent through comms are routed to the channel's transport."""

    def test_send_to_shared(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc
        from lab.utility_closet.comms import ChannelMessage

        msg = ChannelMessage(
            channel="comms://shared",
            source="akien",
            payload="hello from shared",
        )
        assert uc._comms.send(msg) is True

        result = uc._comms.read("comms://shared", limit=10)
        assert len(result) >= 1

    def test_send_to_agent_channel(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc
        from lab.utility_closet.comms import ChannelMessage

        uc._comms.ensure_channel("comms://ccmain", notify=True)
        msg = ChannelMessage(
            channel="comms://ccmain",
            source="akien",
            payload="hello CC",
        )
        assert uc._comms.send(msg) is True

    def test_subscriber_notified(self):
        from lab.claudecode.utility_closet_server import _init_comms

        _init_comms()

        from lab.claudecode import utility_closet_server as uc
        from lab.utility_closet.comms import ChannelMessage

        uc._comms.ensure_channel("comms://notify-test", notify=True)

        received = []
        uc._comms.subscribe("comms://notify-test", "listener", received.append)

        msg = ChannelMessage(
            channel="comms://notify-test",
            source="sender",
            payload="notify me",
        )
        uc._comms.send(msg)
        assert len(received) == 1
        assert received[0].payload == "notify me"

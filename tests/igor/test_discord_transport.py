"""
test_discord_transport.py — T-uc-channel-migration

Tests for Discord comms transport.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_channel(address="comms://discord/123456789"):
    from lab.utility_closet.comms import Channel

    return Channel(address=address)


def _make_message(payload="Hello from Igor"):
    from lab.utility_closet.comms import ChannelMessage

    return ChannelMessage(
        channel="comms://discord/123456789",
        source="igor-wild-0001",
        content_type="text/plain",
        payload=payload,
    )


class TestExtractChannelId:
    def test_valid_id(self):
        from lab.utility_closet.transports.discord import _extract_channel_id

        assert _extract_channel_id("comms://discord/123456789") == 123456789

    def test_invalid_format(self):
        from lab.utility_closet.transports.discord import _extract_channel_id

        assert _extract_channel_id("comms://discord/general") is None

    def test_webhook_address(self):
        from lab.utility_closet.transports.discord import _extract_channel_id

        assert _extract_channel_id("comms://discord/webhook") is None


class TestDiscordTransport:
    def test_send_routes_to_bot(self):
        from lab.utility_closet.transports.discord import DiscordTransport

        transport = DiscordTransport()
        mock_bot = MagicMock()
        transport._bot = mock_bot

        channel = _make_channel()
        msg = _make_message("test message")

        result = transport.send(channel, msg)
        assert result is True
        mock_bot.send.assert_called_once_with(123456789, "test message")
        assert transport._send_count == 1

    def test_send_empty_payload_returns_false(self):
        from lab.utility_closet.transports.discord import DiscordTransport

        transport = DiscordTransport()
        transport._bot = MagicMock()

        channel = _make_channel()
        msg = _make_message("")

        result = transport.send(channel, msg)
        assert result is False

    def test_send_invalid_address_returns_false(self):
        from lab.utility_closet.transports.discord import DiscordTransport

        transport = DiscordTransport()
        transport._bot = MagicMock()

        channel = _make_channel("comms://discord/not-a-number")
        msg = _make_message("test")

        result = transport.send(channel, msg)
        assert result is False

    def test_send_bot_unavailable(self):
        from lab.utility_closet.transports.discord import DiscordTransport

        transport = DiscordTransport()
        transport._bot = False  # sentinel for failed import

        channel = _make_channel()
        msg = _make_message("test")

        result = transport.send(channel, msg)
        assert result is False

    def test_send_exception_handled(self):
        from lab.utility_closet.transports.discord import DiscordTransport

        transport = DiscordTransport()
        mock_bot = MagicMock()
        mock_bot.send.side_effect = RuntimeError("connection lost")
        transport._bot = mock_bot

        channel = _make_channel()
        msg = _make_message("test")

        result = transport.send(channel, msg)
        assert result is False

    def test_read_returns_empty(self):
        from lab.utility_closet.transports.discord import DiscordTransport

        transport = DiscordTransport()
        channel = _make_channel()
        assert transport.read(channel) == []

    def test_close_is_noop(self):
        from lab.utility_closet.transports.discord import DiscordTransport

        transport = DiscordTransport()
        transport.close()  # should not raise

    def test_lazy_bot_loading(self):
        from lab.utility_closet.transports.discord import DiscordTransport

        transport = DiscordTransport()
        assert transport._bot is None

        # Simulate import failure
        with patch(
            "lab.utility_closet.transports.discord.DiscordTransport._get_bot",
            return_value=None,
        ):
            channel = _make_channel()
            msg = _make_message("test")
            result = transport.send(channel, msg)
            assert result is False

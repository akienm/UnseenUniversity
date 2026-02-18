"""
Discord tool - lets Igor send messages to Discord channels.
Works with the discord_bot background thread via the outgoing queue.
"""

from .registry import Tool, registry
from ..network import discord_bot


def send_discord_message(channel_id: int, text: str) -> str:
    """Send a message to a Discord channel."""
    if not discord_bot.is_running():
        return "Discord bot is not running (check DISCORD_BOT_TOKEN in .env)"
    discord_bot.send(channel_id, text)
    return f"Queued message to channel {channel_id}: {text[:60]}{'...' if len(text) > 60 else ''}"


registry.register(Tool(
    name="send_discord_message",
    description="Send a message to a Discord channel. Use the channel_id from the incoming message context.",
    parameters={
        "type": "object",
        "properties": {
            "channel_id": {"type": "integer", "description": "Discord channel ID to send to"},
            "text": {"type": "string", "description": "Message text to send"},
        },
        "required": ["channel_id", "text"],
    },
    fn=send_discord_message,
))

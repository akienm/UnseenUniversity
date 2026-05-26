"""
Discord tool - lets Igor send messages to Discord channels.
T-igor-network-remove: discord_bot is no longer in-process. This tool
returns a disabled message until unseen_university IPC ships.
"""

from devices.igor.tools.registry import Tool, registry


def send_discord_message(channel_id: int, text: str) -> str:
    """Send a message to a Discord channel."""
    try:
        from ..network import discord_bot

        if not discord_bot.is_running():
            return "Discord bot is not running (check DISCORD_BOT_TOKEN in .env)"
        discord_bot.send(channel_id, text)
        return f"Queued message to channel {channel_id}: {text[:60]}{'...' if len(text) > 60 else ''}"
    except ImportError:
        return "Discord bot not available (network module removed; unseen_university IPC pending)"


registry.register(
    Tool(
        name="send_discord_message",
        description="Send a message to a Discord channel. Use the channel_id from the incoming message context.",
        parameters={
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "integer",
                    "description": "Discord channel ID to send to",
                },
                "text": {"type": "string", "description": "Message text to send"},
            },
            "required": ["channel_id", "text"],
        },
        fn=send_discord_message,
    )
)

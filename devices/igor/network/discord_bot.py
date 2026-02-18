"""
Discord bot - runs in a background thread alongside Igor's REPL.
Incoming messages are queued for Igor to process.
Igor can send messages back via the send_discord_message tool.
"""

import asyncio
import os
import queue
import threading
from dataclasses import dataclass

import discord

# Thread-safe queues between Discord bot and Igor's main loop
incoming: queue.Queue = queue.Queue()   # Discord → Igor
outgoing: queue.Queue = queue.Queue()   # Igor → Discord

_bot_thread: threading.Thread | None = None
_client: discord.Client | None = None
_loop: asyncio.AbstractEventLoop | None = None


@dataclass
class DiscordMessage:
    content: str
    author: str
    channel_id: int
    channel_name: str
    guild_name: str
    message_id: int


def send(channel_id: int, text: str):
    """Queue a message to be sent to Discord. Thread-safe."""
    outgoing.put((channel_id, text))


class IgorBot(discord.Client):
    def __init__(self, allowed_channel_id: int | None = None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.allowed_channel_id = allowed_channel_id
        self.guild_id = int(os.getenv("DISCORD_GUILD_ID", "0"))

    async def on_ready(self):
        guild = discord.utils.get(self.guilds, id=self.guild_id)
        scope = f"#{self.allowed_channel_id}" if self.allowed_channel_id else "all channels"
        print(f"[Discord] Connected as {self.user} | Server: {guild.name if guild else '?'} | Scope: {scope}")
        # Start outgoing message pump
        self.loop.create_task(self._pump_outgoing())

    async def on_message(self, message: discord.Message):
        # Ignore own messages
        if message.author == self.user:
            return
        # Ignore wrong guild
        if self.guild_id and message.guild and message.guild.id != self.guild_id:
            return
        # Ignore wrong channel (if restricted)
        if self.allowed_channel_id and message.channel.id != self.allowed_channel_id:
            return

        incoming.put(DiscordMessage(
            content=message.content,
            author=str(message.author),
            channel_id=message.channel.id,
            channel_name=str(message.channel),
            guild_name=message.guild.name if message.guild else "DM",
            message_id=message.id,
        ))

    async def _pump_outgoing(self):
        """Poll outgoing queue and send messages to Discord."""
        while True:
            try:
                channel_id, text = outgoing.get_nowait()
                channel = self.get_channel(channel_id)
                if channel:
                    # Split long messages
                    for chunk in [text[i:i+1900] for i in range(0, len(text), 1900)]:
                        await channel.send(chunk)
            except queue.Empty:
                pass
            await asyncio.sleep(0.5)


def start():
    """Start the Discord bot in a background thread. Non-blocking."""
    global _bot_thread, _client

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        print("[Discord] No DISCORD_BOT_TOKEN set — Discord disabled.")
        return

    channel_id_str = os.getenv("DISCORD_CHANNEL_ID", "").strip()
    allowed_channel = int(channel_id_str) if channel_id_str else None

    _client = IgorBot(allowed_channel_id=allowed_channel)

    def run():
        asyncio.run(_client.start(token))

    _bot_thread = threading.Thread(target=run, daemon=True, name="discord-bot")
    _bot_thread.start()


def is_running() -> bool:
    return _bot_thread is not None and _bot_thread.is_alive()

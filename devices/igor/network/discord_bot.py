"""
Discord bot - runs in a background thread alongside Igor's REPL.
Incoming messages are queued for Igor to process.
Igor can send messages back via the send_discord_message tool.

Forensic logging: every inbound and outbound event is logged to
  ~/.TheIgors/<instance>/logs/discord.log
so we can diagnose dropped replies after the fact.

Outgoing delivery modes (in priority order):
  1. Webhook  — if DISCORD_WEBHOOK_URL is set; simple HTTP POST, no bot required
  2. Bot send — existing discord.py channel.send() via the bot client
"""

import asyncio
import logging
import os
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import discord
import aiohttp

from ..cognition.forensic_logger import log_error

# ── Forensic logger ──────────────────────────────────────────────────────────

from ..paths import paths as _paths_fn

_LOG_DIR = _paths_fn().logs
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_PATH = _LOG_DIR / "discord.log"

_discord_log = logging.getLogger("igor.discord")
if not _discord_log.handlers:
    _discord_log.setLevel(logging.DEBUG)
    _fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _discord_log.addHandler(_fh)
    _discord_log.propagate = False


def _log(event: str, **kwargs):
    parts = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
    _discord_log.info(f"event={event} {parts}")


# ── Thread-safe queues between Discord bot and Igor's main loop ───────────────

incoming: queue.Queue = queue.Queue()  # Discord → Igor
outgoing: queue.Queue = queue.Queue()  # Igor → Discord

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
    _log("send_queued", channel_id=channel_id, text_len=len(text), preview=text[:60])
    outgoing.put((channel_id, text))


class IgorBot(discord.Client):
    def __init__(self, allowed_channel_id: int | None = None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.allowed_channel_id = allowed_channel_id
        self.guild_id = int(os.getenv("DISCORD_GUILD_ID", "0"))
        self._webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

    async def on_ready(self):
        guild = discord.utils.get(self.guilds, id=self.guild_id)
        scope = (
            f"#{self.allowed_channel_id}" if self.allowed_channel_id else "all channels"
        )
        webhook_note = " | webhook=enabled" if self._webhook_url else ""
        msg = f"[Discord] Connected as {self.user} | Server: {guild.name if guild else '?'} | Scope: {scope}{webhook_note}"
        print(msg)
        _log(
            "bot_ready",
            user=str(self.user),
            guild=guild.name if guild else "?",
            scope=scope,
            webhook=bool(self._webhook_url),
        )
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

        _log(
            "msg_received",
            author=str(message.author),
            channel=str(message.channel),
            message_id=message.id,
            content_len=len(message.content),
            preview=message.content[:80],
        )

        incoming.put(
            DiscordMessage(
                content=message.content,
                author=str(message.author),
                channel_id=message.channel.id,
                channel_name=str(message.channel),
                guild_name=message.guild.name if message.guild else "DM",
                message_id=message.id,
            )
        )

    async def _send_via_webhook(self, text: str) -> bool:
        """Send text via webhook. Returns True on success."""
        if not self._webhook_url:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                payload = {"content": text}
                async with session.post(
                    self._webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    ok = resp.status in (200, 204)
                    _log(
                        "webhook_send",
                        status=resp.status,
                        ok=ok,
                        text_len=len(text),
                        preview=text[:60],
                    )
                    return ok
        except Exception as exc:
            _log("webhook_send_error", error=str(exc), preview=text[:60])
            return False

    async def _pump_outgoing(self):
        """Poll outgoing queue and send messages to Discord."""
        while True:
            try:
                channel_id, text = outgoing.get_nowait()
                chunks = [text[i : i + 1900] for i in range(0, len(text), 1900)]

                for chunk in chunks:
                    sent = False

                    # Try webhook first (if configured)
                    if self._webhook_url:
                        sent = await self._send_via_webhook(chunk)

                    # Fall back to bot channel.send().
                    # Use fetch_channel() (API call) not get_channel() (cache only).
                    # get_channel() misses DM channels entirely and misses guild channels
                    # that weren't cached at ready time — causing both known #144 bugs.
                    if not sent:
                        try:
                            channel = await self.fetch_channel(channel_id)
                            await channel.send(chunk)
                            _log(
                                "bot_send_ok",
                                channel_id=channel_id,
                                text_len=len(chunk),
                                preview=chunk[:60],
                            )
                            sent = True
                        except discord.Forbidden as exc:
                            _log(
                                "bot_send_forbidden",
                                channel_id=channel_id,
                                error=str(exc),
                                hint="check bot permissions / DM privacy settings",
                            )
                        except discord.NotFound as exc:
                            _log(
                                "bot_send_not_found",
                                channel_id=channel_id,
                                error=str(exc),
                            )
                        except Exception as exc:
                            _log(
                                "bot_send_error",
                                channel_id=channel_id,
                                error=str(exc),
                                preview=chunk[:60],
                            )

                    if not sent:
                        _log(
                            "msg_dropped",
                            channel_id=channel_id,
                            webhook=bool(self._webhook_url),
                            preview=chunk[:60],
                        )

            except queue.Empty:
                pass  # expected — outgoing queue is empty, poll again
            await asyncio.sleep(0.5)


def start():
    """Start the Discord bot in a background thread. Non-blocking."""
    global _bot_thread, _client

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        print("[Discord] No DISCORD_BOT_TOKEN set — Discord disabled.")
        _log("bot_disabled", reason="no_token")
        return

    channel_id_str = os.getenv("DISCORD_CHANNEL_ID", "").strip()
    allowed_channel = int(channel_id_str) if channel_id_str else None

    _client = IgorBot(allowed_channel_id=allowed_channel)

    def run():
        _log("bot_thread_start")
        try:
            asyncio.run(_client.start(token))
        except Exception as exc:
            _log("bot_thread_crash", error=str(exc))

    _bot_thread = threading.Thread(target=run, daemon=True, name="discord-bot")
    _bot_thread.start()
    _log("bot_thread_launched", allowed_channel=allowed_channel)
    try:
        from ..cognition.daemon_supervisor import supervisor as _sup

        _sup.register("discord-bot", _bot_thread, health_fn=is_running)
    except Exception as e:
        log_error(
            kind="TOOL_FAIL", detail=f"daemon supervisor registration failed: {e}"
        )


def is_running() -> bool:
    return _bot_thread is not None and _bot_thread.is_alive()

"""
Discord bot — standalone module for unseen_university.

Adapted from wild_igor/igor/network/discord_bot.py.
All TheIgors imports removed; uses ADC_RUNTIME_ROOT for paths.

Incoming messages are queued in `incoming` for any consumer.
Outgoing messages are sent via `send(channel_id, text)`.
"""

import asyncio
import logging
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

import discord
import aiohttp

_RUNTIME_ROOT = Path(
    os.environ.get("ADC_RUNTIME_ROOT")
    or os.environ.get("IGOR_RUNTIME_ROOT")
    or Path.home() / ".unseen_university"
)
_LOG_DIR = _RUNTIME_ROOT / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_PATH = _LOG_DIR / "discord.log"

_discord_log = logging.getLogger("adc.discord")
if not _discord_log.handlers:
    _discord_log.setLevel(logging.DEBUG)
    _fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _discord_log.addHandler(_fh)
    _discord_log.propagate = False


def _log(event: str, **kwargs):
    parts = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
    _discord_log.info(f"event={event} {parts}")


# ── Thread-safe queues ────────────────────────────────────────────────────────

incoming: queue.Queue = queue.Queue()  # Discord → consumer
outgoing: queue.Queue = queue.Queue()  # caller → Discord

_bot_thread: threading.Thread | None = None
_client: discord.Client | None = None
_stop_event = threading.Event()


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


class AgentBot(discord.Client):
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
        _log(
            "bot_ready",
            user=str(self.user),
            guild=guild.name if guild else "?",
            scope=scope,
            webhook=bool(self._webhook_url),
        )
        print(
            f"[Discord] Connected as {self.user} | "
            f"Server: {guild.name if guild else '?'} | "
            f"Scope: {scope}{webhook_note}"
        )
        self.loop.create_task(self._pump_outgoing())

    async def on_disconnect(self):
        _log("bot_disconnected", note="discord.py will attempt auto-reconnect")

    async def on_error(self, event, *args, **kwargs):
        import traceback

        _log("bot_error", event=event, traceback=traceback.format_exc()[:500])

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return
        if self.guild_id and message.guild and message.guild.id != self.guild_id:
            return
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
        while not _stop_event.is_set():
            try:
                channel_id, text = outgoing.get_nowait()
                chunks = [text[i : i + 1900] for i in range(0, len(text), 1900)]

                for chunk in chunks:
                    sent = False

                    if self._webhook_url:
                        sent = await self._send_via_webhook(chunk)

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
                pass
            await asyncio.sleep(0.5)


def start():
    """Start the Discord bot in a background thread. Non-blocking."""
    global _bot_thread, _client

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        _log("bot_disabled", reason="no_token")
        return

    _stop_event.clear()
    channel_id_str = os.getenv("DISCORD_CHANNEL_ID", "").strip()
    allowed_channel = int(channel_id_str) if channel_id_str else None

    _client = AgentBot(allowed_channel_id=allowed_channel)

    def run():
        import time as _time

        _log("bot_thread_start")
        _retry_delay = 5
        _max_delay = 300
        while not _stop_event.is_set():
            try:
                asyncio.run(_client.start(token))
                _log("bot_thread_clean_exit")
                break
            except Exception as exc:
                if _stop_event.is_set():
                    break
                _log(
                    "bot_thread_crash",
                    error=str(exc),
                    retry_in=_retry_delay,
                )
                _time.sleep(_retry_delay)
                _retry_delay = min(_retry_delay * 2, _max_delay)
                _client.__init__(allowed_channel_id=allowed_channel)

    _bot_thread = threading.Thread(target=run, daemon=True, name="adc-discord-bot")
    _bot_thread.start()
    _log("bot_thread_launched", allowed_channel=allowed_channel)


def stop():
    """Signal the bot to stop. Daemon thread will exit on next iteration."""
    _stop_event.set()
    _log("bot_stop_requested")


def is_running() -> bool:
    return _bot_thread is not None and _bot_thread.is_alive()


def log_path() -> str:
    return str(_LOG_PATH)

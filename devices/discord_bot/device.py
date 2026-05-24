"""
DiscordBotDevice — rack device for the unseen_university Discord bot.

Phase 5: bot code lives in bot.py (this package). The device starts the bot
thread and monitors health via is_running() + discord.log recency.

Configuration:
  DISCORD_BOT_TOKEN     — required; bot disabled without it
  DISCORD_CHANNEL_ID    — optional; restrict to one channel
  DISCORD_GUILD_ID      — optional; restrict to one guild
  DISCORD_WEBHOOK_URL   — optional; enables webhook delivery mode
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION

from . import bot as _bot

_START_TIME = time.time()
_LOG_HEALTHY_WINDOW = 300  # 5 min


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DiscordBotDevice(BaseDevice):
    """
    Rack device for the Discord bot.

    The bot runs as a daemon thread (bot.py) within the unseen_university
    process. Health is measured via is_running() + discord.log recency.
    """

    DEVICE_ID = "discord-bot"

    def __init__(self) -> None:
        super().__init__()
        self._blocked = False
        self._block_reason = ""
        self._startup_errors: list[str] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if _bot.is_running():
            return
        try:
            _bot.start()
        except Exception as exc:
            self._startup_errors.append(str(exc))

    def stop(self) -> None:
        _bot.stop()

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "DiscordBot",
            "version": "1.0.0",
            "purpose": "Discord channel I/O via webhook + bot send; runs as daemon thread",
        }

    def requirements(self) -> dict:
        return {
            "deps": ["discord.py>=2.0", "aiohttp"],
            "system": ["DISCORD_BOT_TOKEN env var set"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": ["discord_send", "discord_receive"],
            "mcp_endpoint": None,
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_write",
            "supports_push": True,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        if self._blocked:
            return {
                "status": "unhealthy",
                "detail": f"blocked: {self._block_reason}",
                "checked_at": _now(),
            }
        if not os.environ.get("DISCORD_BOT_TOKEN"):
            return {
                "status": "degraded",
                "detail": "DISCORD_BOT_TOKEN not set — bot disabled",
                "checked_at": _now(),
            }
        if not _bot.is_running():
            return {
                "status": "unhealthy",
                "detail": "bot thread not running",
                "checked_at": _now(),
            }
        log = _bot.log_path()
        try:
            age = time.time() - os.path.getmtime(log)
            if age > _LOG_HEALTHY_WINDOW:
                return {
                    "status": "degraded",
                    "detail": f"discord.log stale ({age:.0f}s) — bot may be stuck",
                    "checked_at": _now(),
                }
            return {
                "status": "healthy",
                "detail": f"thread alive, discord.log fresh ({age:.0f}s ago)",
                "checked_at": _now(),
            }
        except OSError:
            return {
                "status": "degraded",
                "detail": f"discord.log not found at {log}",
                "checked_at": _now(),
            }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._startup_errors)

    def logs(self) -> dict:
        return {"paths": {"discord": _bot.log_path()}}

    def update_info(self) -> dict:
        return {"current_version": "1.0.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": "localhost",
            "pid": os.getpid(),
            "launch_command": "thread:adc-discord-bot (daemon)",
        }

    def restart(self) -> None:
        self._blocked = False
        self._block_reason = ""
        self.stop()
        self.start()

    def block(self, reason: str) -> None:
        self._blocked = True
        self._block_reason = reason

    def halt(self) -> None:
        self.stop()
        self._blocked = True
        self._block_reason = "halt requested"

    def recovery(self) -> None:
        self._blocked = False
        self._block_reason = ""
        if not _bot.is_running():
            self.start()

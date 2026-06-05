"""
DiscordBotShim — lifecycle proxy for the Discord bot (Phase 5).

Phase 5: bot runs as a daemon thread in bot.py; this shim owns start/stop.
"""

from __future__ import annotations

import logging
import os

from unseen_university.shim import BaseShim
from config.device_config import DeviceConfig
from skeleton.registry import DeviceRegistry

from .device import DiscordBotDevice
from . import bot as _bot

log = logging.getLogger(__name__)


class DiscordBotShim(BaseShim):
    DEVICE_ID = "discord-bot"

    def __init__(self, registry: DeviceRegistry | None = None) -> None:
        self._device = DiscordBotDevice()
        self._registry = registry or DeviceRegistry()

    @property
    def device_id(self) -> str:
        return self.DEVICE_ID

    @property
    def device(self) -> DiscordBotDevice:
        return self._device

    def start(self) -> bool:
        try:
            self._device.start()
            self._registry.register(
                device_id="discord-bot",
                config=DeviceConfig(),
                mailbox="comms://discord-bot/inbox",
                name="DiscordBot",
                agent_class="utility",
            )
            log.info("DiscordBotShim: started, registered with skeleton")
            return _bot.is_running() or not os.environ.get("DISCORD_BOT_TOKEN")
        except Exception:
            log.exception("DiscordBotShim.start() failed")
            return False

    def stop(self) -> bool:
        try:
            self._device.stop()
            return True
        except Exception:
            log.exception("DiscordBotShim.stop() failed")
            return False

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def self_test(self) -> dict:
        token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if not token:
            return {"passed": False, "details": "DISCORD_BOT_TOKEN not set"}
        if not _bot.is_running():
            return {"passed": False, "details": "bot thread not running"}
        return {"passed": True, "details": "token set, bot thread alive"}

    def rollback(self) -> None:
        self._device.stop()

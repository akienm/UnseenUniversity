"""
Notification config — delivery level control for UU device shims.

Every device has a notifications.cfg at ~/.unseen_university/<device>/notifications.cfg
that controls how incoming bus messages are delivered. The shim reads this config on
each message to decide: queue silently, queue + surface at next break, or interrupt now.

Config format (INI):
    [defaults]
    level = QUIET

    [overrides]
    granny-weatherwax = QUIET
    akien = LOUD

Delivery levels:
    SILENT — message queued in mailbox only; agent pulls on demand
    QUIET  — message queued; surfaced at next natural break (savestate, context-load, idle)
    LOUD   — immediate tmux send-keys to own session (falls back to QUIET if no session)
"""

from __future__ import annotations

import configparser
import logging
import os
import tempfile
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)

_CONFIG_FILENAME = "notifications.cfg"


class DeliveryMode(str, Enum):
    SILENT = "SILENT"
    QUIET = "QUIET"
    LOUD = "LOUD"

    @classmethod
    def _missing_(cls, value: object) -> "DeliveryMode":
        if isinstance(value, str):
            try:
                return cls[value.upper()]
            except KeyError:
                pass
        return cls.QUIET


_DEFAULT_LEVEL = DeliveryMode.QUIET

_DEFAULT_CONFIG_TEXT = """\
[defaults]
# SILENT = mailbox only, agent pulls on demand
# QUIET  = queue + surface at next natural break
# LOUD   = immediate tmux interrupt (falls back to QUIET if no session)
level = QUIET

[overrides]
# Per-sender level overrides. Override wins over default.
# Examples:
#   granny-weatherwax = QUIET
#   akien = LOUD
#   igor = SILENT
"""


class NotificationConfig:
    """Notification delivery config for one device instance."""

    def __init__(
        self,
        default_level: DeliveryMode = _DEFAULT_LEVEL,
        overrides: dict[str, DeliveryMode] | None = None,
    ) -> None:
        self.default_level = default_level
        self.overrides: dict[str, DeliveryMode] = overrides or {}

    # ── Factories ─────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, device_home: Path) -> "NotificationConfig":
        """Load config from device_home/notifications.cfg.

        Creates a default config file if none exists (idempotent).
        Never raises — returns defaults on any read or parse error.
        """
        cfg_path = device_home / _CONFIG_FILENAME
        if not cfg_path.exists():
            cls.write_default(device_home)
            log.info("NotificationConfig: created default config at %s", cfg_path)
            return cls()

        parser = configparser.ConfigParser()
        try:
            parser.read(cfg_path, encoding="utf-8")
        except Exception as exc:
            log.warning("NotificationConfig: parse error in %s — using defaults: %s", cfg_path, exc)
            return cls()

        default_level = _DEFAULT_LEVEL
        if parser.has_option("defaults", "level"):
            raw = parser.get("defaults", "level").strip()
            default_level = DeliveryMode(raw)

        overrides: dict[str, DeliveryMode] = {}
        if parser.has_section("overrides"):
            for sender, raw_level in parser.items("overrides"):
                overrides[sender] = DeliveryMode(raw_level.strip())

        return cls(default_level=default_level, overrides=overrides)

    # ── Query ──────────────────────────────────────────────────────────────────

    def get_level(self, sender: str) -> DeliveryMode:
        """Return the effective delivery level for a given sender.

        Per-sender override wins over default.
        """
        return self.overrides.get(sender, self.default_level)

    # ── Persistence ───────────────────────────────────────────────────────────

    @staticmethod
    def write_default(device_home: Path) -> None:
        """Write a default notifications.cfg to device_home.

        Uses atomic write (tmp + rename) so a partial write never leaves a
        corrupt config. Idempotent — safe to call if the file already exists.
        """
        device_home.mkdir(parents=True, exist_ok=True)
        cfg_path = device_home / _CONFIG_FILENAME
        _atomic_write(cfg_path, _DEFAULT_CONFIG_TEXT)

    def save(self, device_home: Path) -> None:
        """Write current config state back to device_home/notifications.cfg."""
        parser = configparser.ConfigParser()
        parser["defaults"] = {"level": self.default_level.value}
        if self.overrides:
            parser["overrides"] = {k: v.value for k, v in self.overrides.items()}

        lines: list[str] = []
        for section in parser.sections():
            lines.append(f"[{section}]")
            for key, val in parser.items(section):
                lines.append(f"{key} = {val}")
            lines.append("")

        device_home.mkdir(parents=True, exist_ok=True)
        _atomic_write(device_home / _CONFIG_FILENAME, "\n".join(lines) + "\n")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path via tmp + rename — never leaves a partial file."""
    dir_ = path.parent
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".notify_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

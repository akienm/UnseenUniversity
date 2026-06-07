"""
NotificationDispatcher — incoming message delivery for UU device shims.

Reads NotificationConfig for the device and executes delivery:
  SILENT — log only; message remains in mailbox for agent to pull
  QUIET  — log + append to notify_pending.txt; surfaced at next break
  LOUD   — tmux send-keys interrupt to own session (falls back to QUIET
            when tmux session is absent or unreachable)

State-linked defaults: when is_busy_fn() returns True, effective default
is SILENT regardless of config. When idle (or is_busy_fn not set), the
config default applies.

Every delivery decision is logged at INFO:
  notif: <sender> → LOUD (reason: override:akien)
  notif: <sender> → QUIET (reason: state:busy:no-tmux-fallback)

Usage (in a device shim's start()):
    self._notifier = NotificationDispatcher(
        device_home=Path.home() / ".unseen_university" / "CC-wild-0001",
        tmux_session=os.environ.get("CC_TMUX_SESSION"),
        is_busy_fn=lambda: (Path.home() / ".granny/available/CC.0.available.false").exists(),
    )
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .notify import DeliveryMode, NotificationConfig

log = logging.getLogger(__name__)

_PENDING_FILENAME = "notify_pending.txt"


class NotificationDispatcher:
    """Delivery executor for one device instance."""

    def __init__(
        self,
        device_home: Path,
        tmux_session: str | None = None,
        is_busy_fn: Callable[[], bool] | None = None,
        pending_path: Path | None = None,
    ) -> None:
        """
        Args:
            device_home: device runtime home (config + pending queue live here)
            tmux_session: tmux session name for LOUD delivery
                          (falls back to CC_TMUX_SESSION env var)
            is_busy_fn: callable returning True when agent is working
                        (SILENT default when busy; config default when idle)
            pending_path: override path for the QUIET pending queue file
        """
        self._device_home = device_home
        self._tmux_session = tmux_session or os.environ.get("CC_TMUX_SESSION")
        self._is_busy_fn = is_busy_fn
        self._pending_path = pending_path or (device_home / _PENDING_FILENAME)

    # ── Public interface ───────────────────────────────────────────────────────

    def filter(self, sender: str) -> tuple[DeliveryMode, str]:
        """Return (level, reason) for an incoming message from sender.

        Priority: per-sender override > state-linked default > config default.
        """
        cfg = NotificationConfig.load(self._device_home)

        if sender in cfg.overrides:
            return cfg.overrides[sender], f"override:{sender}"

        if self._is_busy_fn is not None and self._is_busy_fn():
            return DeliveryMode.SILENT, "state:busy"

        return cfg.default_level, "config:default"

    def deliver(self, sender: str, summary: str) -> DeliveryMode:
        """Execute delivery for an incoming message. Returns effective level used.

        Logs every decision at INFO for observability (AR-009).
        """
        level, reason = self.filter(sender)
        effective = level

        if level == DeliveryMode.LOUD:
            if self._tmux_interrupt(summary):
                log.info("notif: %s → LOUD (reason: %s)", sender, reason)
                return DeliveryMode.LOUD
            # Fallback: no tmux session or send failed
            effective = DeliveryMode.QUIET
            reason = f"{reason}:no-tmux-fallback"

        if effective == DeliveryMode.QUIET:
            self._queue_pending(sender, summary)
            log.info("notif: %s → QUIET (reason: %s)", sender, reason)
            return DeliveryMode.QUIET

        # SILENT
        log.info("notif: %s → SILENT (reason: %s)", sender, reason)
        return DeliveryMode.SILENT

    def drain_pending(self) -> list[dict]:
        """Read and clear the QUIET pending queue.

        Returns list of {ts, sender, summary} dicts. Clears the file after read.
        Safe to call when no pending entries exist — returns empty list.
        """
        if not self._pending_path.exists():
            return []
        try:
            lines = self._pending_path.read_text(encoding="utf-8").splitlines()
            entries = []
            for line in lines:
                parts = line.split(" | ", 2)
                if len(parts) == 3:
                    entries.append({"ts": parts[0], "sender": parts[1], "summary": parts[2]})
            self._pending_path.unlink()
            return entries
        except Exception as exc:
            log.warning("notif: drain_pending error: %s", exc)
            return []

    # ── Private helpers ────────────────────────────────────────────────────────

    def _tmux_interrupt(self, summary: str) -> bool:
        """Send a LOUD interrupt to the tmux session.

        Protocol: 3× Enter (clears in-progress input) then the summary text.
        The summary is typed but NOT submitted (no trailing Enter) — the agent
        sees the message in the input buffer and decides whether to act on it.

        Returns True on success, False when session absent or send fails.
        """
        session = self._tmux_session
        if not session:
            log.debug("notif: LOUD requested but CC_TMUX_SESSION not set")
            return False

        # Guard: check session exists before sending
        check = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True,
        )
        if check.returncode != 0:
            log.debug("notif: LOUD requested but tmux session %r not found", session)
            return False

        try:
            for _ in range(3):
                subprocess.run(
                    ["tmux", "send-keys", "-t", session, "", "Enter"],
                    capture_output=True,
                    check=True,
                )
            subprocess.run(
                ["tmux", "send-keys", "-t", session, f"[notif] {summary}", ""],
                capture_output=True,
                check=True,
            )
            return True
        except subprocess.CalledProcessError as exc:
            log.warning("notif: tmux send-keys failed: %s", exc)
            return False

    def _queue_pending(self, sender: str, summary: str) -> None:
        """Append one entry to the QUIET pending queue file."""
        try:
            self._pending_path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with open(self._pending_path, "a", encoding="utf-8") as f:
                f.write(f"{ts} | {sender} | {summary}\n")
        except Exception as exc:
            log.warning("notif: failed to queue pending entry: %s", exc)

"""
SudoRelayDevice — rack device wrapper for the sudo relay daemon.

States:
  OFF         — tmux session does not exist
  NEEDPW      — session running, sudo password prompt visible (awaiting /pw)
  WAITING     — daemon loop running, watching for pending.sh
  PROCESSING  — pending.sh present; a privileged command is executing

Chat interface (slash-commands only, like Bark!):
  /start      — start the daemon (guru-only by convention)
  /stop       — stop the daemon
  /status     — return current state
  /pw <pass>  — send password to NEEDPW prompt via tmux send-keys (never logged)
  <free text> — 'Sorry nice person, no replies available for that.'
"""

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_UU_ROOT = Path(__file__).resolve().parents[2]
_SUDO_RELAY_CLI = _UU_ROOT / "sudo_relay"
_TMUX_SESSION = "sudo-relay"
_PENDING_SH = Path.home() / ".unseen_university" / "sudo_relay" / "pending.sh"
_DONE_FILE = Path.home() / ".unseen_university" / "sudo_relay" / "done"

_CANNED_RESPONSE = "Sorry nice person, no replies available for that."


def _session_exists() -> bool:
    r = subprocess.run(
        ["tmux", "has-session", "-t", _TMUX_SESSION],
        capture_output=True,
    )
    return r.returncode == 0


def _pane_text() -> str:
    """Return the last 5 lines of the sudo-relay pane. Returns '' on error."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", _TMUX_SESSION, "-p"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


class SudoRelayDevice:
    """Rack device wrapper for the sudo relay daemon."""

    def state(self) -> str:
        """Return current state string: OFF | NEEDPW | WAITING | PROCESSING."""
        if not _session_exists():
            return "OFF"
        if _PENDING_SH.exists():
            return "PROCESSING"
        pane = _pane_text().lower()
        if "password" in pane or "[sudo]" in pane or "needpw" in pane:
            return "NEEDPW"
        return "WAITING"

    def handle_chat(self, message: str) -> str:
        """Process a chat message. Returns response string."""
        stripped = message.strip()
        lower = stripped.lower()

        if not lower.startswith("/"):
            return _CANNED_RESPONSE

        if lower == "/start" or lower.startswith("/start "):
            return self._cmd_start()
        if lower == "/stop" or lower.startswith("/stop "):
            return self._cmd_stop()
        if lower == "/status":
            return f"sudo-relay state: {self.state()}"
        if lower.startswith("/pw "):
            password = stripped[4:]
            return self._cmd_pw(password)
        if lower == "/pw":
            return "Usage: /pw <password>"

        return _CANNED_RESPONSE

    def _cmd_start(self) -> str:
        if _session_exists():
            return f"Already running (state: {self.state()})"
        try:
            subprocess.run(
                [str(_SUDO_RELAY_CLI), "--no-attach"],
                check=True,
                timeout=10,
            )
            log.info("SudoRelayDevice: started daemon")
            return f"Started. State: {self.state()}"
        except Exception as exc:
            log.warning("SudoRelayDevice: start failed: %s", exc)
            return f"Start failed: {exc}"

    def _cmd_stop(self) -> str:
        if not _session_exists():
            return "Not running (already OFF)"
        try:
            subprocess.run(
                [str(_SUDO_RELAY_CLI), "--stop"],
                check=True,
                timeout=10,
            )
            log.info("SudoRelayDevice: stopped daemon")
            return "Stopped."
        except Exception as exc:
            log.warning("SudoRelayDevice: stop failed: %s", exc)
            return f"Stop failed: {exc}"

    def _cmd_pw(self, password: str) -> str:
        """Send password to the sudo-relay tmux pane. Password never logged."""
        if not _session_exists():
            return "sudo-relay is OFF — start it first with /start"
        current = self.state()
        if current != "NEEDPW":
            return f"Not in NEEDPW state (currently {current})"
        try:
            # Send password directly via tmux — never captured in our logs.
            subprocess.run(
                ["tmux", "send-keys", "-t", _TMUX_SESSION, password, "Enter"],
                check=True,
                timeout=5,
            )
            log.info("SudoRelayDevice: /pw command sent to tmux pane")
            return "Password sent. Check /status in a moment."
        except Exception as exc:
            log.warning("SudoRelayDevice: /pw failed: %s", exc)
            return f"/pw failed: {exc}"

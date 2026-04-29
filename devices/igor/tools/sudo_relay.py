"""
sudo_relay.py — Igor-side client for the sudoer_daemon.sh relay protocol (D123).

Protocol:
  1. Igor writes a shell script to RELAY_DIR/pending.sh
  2. Daemon picks it up (mv → executing.sh), runs it as sudo, writes exit code to done
  3. Igor polls done, reads exit code, cleans up

Safety:
  - Will not write pending.sh if daemon is not running (no stale relay dir guard)
  - Times out if daemon doesn't respond within timeout_secs
  - Returns (exit_code, output_log) — caller decides what to do with non-zero

Usage (Igor tool call):
  sudo_relay_run(script="apt-get install -y redis-server")
"""

import logging
import os
import time
from pathlib import Path

from .registry import Tool, registry
from ..igor_base import IgorBase

_log = logging.getLogger(__name__)

RELAY_DIR = Path(
    os.environ.get("IGOR_RELAY_DIR", Path.home() / ".TheIgors" / "sudo_relay")
)
DAEMON_LOG = RELAY_DIR / "daemon.log"
PENDING = RELAY_DIR / "pending.sh"
DONE = RELAY_DIR / "done"
EXECUTING = RELAY_DIR / "executing.sh"

DEFAULT_TIMEOUT = 120  # seconds to wait for daemon to finish
POLL_INTERVAL = 1.0  # seconds between done-file checks


def _daemon_appears_running() -> bool:
    """Heuristic: daemon is running if relay dir exists and daemon.log was touched recently."""
    if not RELAY_DIR.exists():
        return False
    if not DAEMON_LOG.exists():
        return False
    age = time.time() - DAEMON_LOG.stat().st_mtime
    # Daemon writes a keepalive every ~2 min; allow 5 min grace
    return age < 300


def sudo_relay_run(script: str, timeout_secs: int = DEFAULT_TIMEOUT) -> dict:
    """
    Submit a shell script to the sudoer_daemon and wait for completion.

    Args:
        script: Shell commands to run as sudo (plain bash, not a file path).
        timeout_secs: Max seconds to wait for the daemon to finish.

    Returns:
        {
            "exit_code": int,
            "log_tail": str,   # last ~20 lines of daemon.log after execution
            "ok": bool,
            "error": str | None
        }
    """
    RELAY_DIR.mkdir(parents=True, exist_ok=True)

    if not _daemon_appears_running():
        return {
            "exit_code": -1,
            "log_tail": "",
            "ok": False,
            "error": (
                "Sudoer daemon does not appear to be running. "
                "Ask Akien to start it: bash ~/TheIgors/wild_igor/igor/tools/sudoer_daemon.sh"
            ),
        }

    # Clear any stale done file from a previous run
    DONE.unlink(missing_ok=True)

    if PENDING.exists() or EXECUTING.exists():
        return {
            "exit_code": -1,
            "log_tail": "",
            "ok": False,
            "error": "Another relay job is already in progress (pending.sh or executing.sh exists).",
        }

    # Write the script
    script_body = "#!/usr/bin/env bash\nset -euo pipefail\n" + script.strip() + "\n"
    PENDING.write_text(script_body)
    PENDING.chmod(0o700)
    _log.info("[sudo_relay] submitted pending.sh (%d bytes)", len(script_body))

    # Poll for done
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        if DONE.exists():
            try:
                exit_code = int(DONE.read_text().strip())
            except ValueError:
                exit_code = -1
            DONE.unlink(missing_ok=True)

            # Grab the tail of the daemon log for context
            log_tail = _tail(DAEMON_LOG, lines=20)
            ok = exit_code == 0
            _log.info("[sudo_relay] done. exit_code=%d ok=%s", exit_code, ok)
            return {
                "exit_code": exit_code,
                "log_tail": log_tail,
                "ok": ok,
                "error": None,
            }

    # Timed out — clean up pending if it wasn't picked up
    PENDING.unlink(missing_ok=True)
    return {
        "exit_code": -1,
        "log_tail": _tail(DAEMON_LOG, lines=20),
        "ok": False,
        "error": f"Timed out after {timeout_secs}s waiting for daemon response.",
    }


def _tail(path: Path, lines: int = 20) -> str:
    if not path.exists():
        return ""
    text = path.read_text(errors="replace")
    return "\n".join(text.splitlines()[-lines:])


# ── Tool registration ──────────────────────────────────────────────────────────


class SudoRelayTool(Tool, IgorBase):
    name = "sudo_relay_run"
    description = (
        "Submit a shell script to the sudoer_daemon for execution as root. "
        "Used for installing software or running privileged commands on a new box. "
        "Requires sudoer_daemon.sh to be running (Akien starts it manually). "
        "Returns exit_code, ok flag, and the last 20 lines of the daemon log."
    )
    parameters = {
        "type": "object",
        "properties": {
            "script": {
                "type": "string",
                "description": "Shell commands to run as sudo (plain bash script body, not a file path).",
            },
            "timeout_secs": {
                "type": "integer",
                "description": "Max seconds to wait for completion (default 120).",
                "default": 120,
            },
        },
        "required": ["script"],
    }

    def run(self, script: str, timeout_secs: int = DEFAULT_TIMEOUT) -> dict:
        return sudo_relay_run(script=script, timeout_secs=timeout_secs)


registry.register(
    Tool(
        name=SudoRelayTool.name,
        description=SudoRelayTool.description,
        parameters=SudoRelayTool.parameters,
        fn=lambda script, timeout_secs=DEFAULT_TIMEOUT: sudo_relay_run(
            script=script, timeout_secs=timeout_secs
        ),
    )
)

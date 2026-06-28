"""
IgorShim — lifecycle management for the Igor agent process.

Start semantics: calls the igor launcher script (IGOR_LAUNCHER env var or
'igor' from PATH). The launcher handles its own tmux session creation and
the exit-42 restart loop.

Stop semantics: sends SIGTERM to the process running inside the tmux session.
The exit-42 loop exits cleanly (exit code != 42) when the main process
receives SIGTERM.

Restart semantics: stop() + start(). The exit-42 loop normally handles
transient crashes — IgorShim.restart() is for operator-directed restarts.

self_test() verifies the tmux session is alive. No deep behavioral check
is performed — Igor's own self_test_log.jsonl tracks internal health.

Configuration via environment variables:
  IGOR_TMUX_SESSION  — tmux session name (default: igor)
  IGOR_LAUNCHER      — path to igor launcher (default: igor from PATH)
  IGOR_HOME          — Igor runtime dir (default: ~/.unseen_university/<IGOR_INSTANCE_ID>)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

from unseen_university.shim import BaseShim

from .paths import paths as _igor_paths

log = logging.getLogger(__name__)

_DEFAULT_SESSION = os.environ.get("IGOR_TMUX_SESSION", "igor")
_DEFAULT_LAUNCHER = os.environ.get("IGOR_LAUNCHER", "igor")
_DEFAULT_HOME = str(_igor_paths().instance)
_STARTUP_TIMEOUT = 15.0


def _tmux_session_alive(session: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True,
            timeout=2,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class IgorShim(BaseShim):
    """
    Manages the Igor agent process lifecycle.

    Usage:
        shim = IgorShim()
        shim.start()            # launches Igor via the igor launcher script
        result = shim.self_test()   # verifies tmux session alive
        shim.stop()             # terminates Igor gracefully
    """

    def __init__(
        self,
        tmux_session: str = _DEFAULT_SESSION,
        launcher: str = _DEFAULT_LAUNCHER,
        igor_home: str = _DEFAULT_HOME,
    ) -> None:
        self._session = tmux_session
        self._launcher = launcher
        self._igor_home = igor_home

    @property
    def device_id(self) -> str:
        return "igor"

    def start(self) -> bool:
        if _tmux_session_alive(self._session):
            log.info("Igor already running in tmux session '%s'", self._session)
            return True

        launcher_path = shutil.which(self._launcher) or self._launcher
        if not os.path.exists(launcher_path) and not shutil.which(self._launcher):
            log.error("Igor launcher not found: %r", self._launcher)
            return False

        try:
            subprocess.run(
                [
                    "tmux",
                    "new-session",
                    "-d",
                    "-s",
                    self._session,
                    launcher_path,
                ],
                check=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as exc:
            log.error("Failed to start Igor in tmux: %s", exc)
            return False

        deadline = time.monotonic() + _STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if _tmux_session_alive(self._session):
                log.info("Igor started in tmux session '%s'", self._session)
                return True
            time.sleep(0.5)

        log.error(
            "Igor launched but tmux session '%s' not confirmed within %.0fs",
            self._session,
            _STARTUP_TIMEOUT,
        )
        return False

    def stop(self) -> bool:
        if not _tmux_session_alive(self._session):
            return True
        try:
            # Kill the pane's process gracefully; tmux session auto-exits
            subprocess.run(
                ["tmux", "send-keys", "-t", self._session, "C-c", ""],
                capture_output=True,
                timeout=5,
            )
            time.sleep(1)
            subprocess.run(
                ["tmux", "kill-session", "-t", self._session],
                capture_output=True,
                timeout=5,
            )
            log.info("Igor stopped (tmux session '%s' killed)", self._session)
            return True
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.error("Failed to stop Igor: %s", exc)
            return False

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        """
        Verify Igor is running in the expected tmux session.

        When Igor is not running, only checks that the launcher binary exists —
        does not launch Igor. Call start() to bring Igor up first.
        """
        if _tmux_session_alive(self._session):
            return {
                "passed": True,
                "details": f"Igor tmux session '{self._session}' is alive",
            }

        launcher_path = shutil.which(self._launcher)
        if launcher_path:
            return {
                "passed": True,
                "details": (
                    f"Launcher found at {launcher_path!r} "
                    f"(Igor not started; call start() first)"
                ),
            }
        return {
            "passed": False,
            "details": f"Igor not running and launcher {self._launcher!r} not found in PATH",
        }

    def rollback(self) -> None:
        """Kill the tmux session if start() failed mid-way."""
        try:
            subprocess.run(
                ["tmux", "kill-session", "-t", self._session],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass
        log.info("IgorShim rollback complete (session '%s' killed)", self._session)

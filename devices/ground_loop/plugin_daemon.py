"""
plugin_daemon.py — Ground Loop daemon-mode plugin manager.

Manages a single background process: polls every N seconds, restarts if dead,
fires on_failure hook after max_restarts consecutive failures.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_IGOR_HOME = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university"))
_FLAGS_DIR = _IGOR_HOME / "flags"
_STDERR_DIR = _IGOR_HOME / "ground_loop" / "logs"
_STDERR_TAIL = 30  # lines of plugin stderr to include in CC recovery prompt


class PluginDaemon:
    """Tracks and restarts a single daemon-mode plugin process."""

    def __init__(self, config: dict) -> None:
        self.name: str = config["name"]
        self.start_cmd: list[str] = config["start_cmd"]
        self.poll_interval: int = int(config.get("poll_interval", 30))
        self.max_restarts: int = int(config.get("max_restarts", 3))
        self.on_failure: Optional[str] = config.get("on_failure")
        self.start_env: dict[str, str] = config.get("start_env", {})
        self._proc: Optional[subprocess.Popen] = None
        self._restart_count: int = 0
        self._tripped: bool = False  # circuit breaker state

    @property
    def breaker_path(self) -> Path:
        return _FLAGS_DIR / f"{self.name}.breaker"

    @property
    def stderr_log_path(self) -> Path:
        return _STDERR_DIR / f"{self.name}.stderr.log"

    def _breaker_tripped(self) -> bool:
        return self.breaker_path.exists()

    def _is_alive(self) -> bool:
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def _spawn(self) -> None:
        env = {**os.environ, **self.start_env}
        log.info("GROUND_LOOP|plugin=%s|action=spawn|cmd=%s", self.name, self.start_cmd)
        _STDERR_DIR.mkdir(parents=True, exist_ok=True)
        stderr_fh = open(self.stderr_log_path, "a")
        self._proc = subprocess.Popen(
            self.start_cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=stderr_fh,
        )
        log.info("GROUND_LOOP|plugin=%s|action=spawned|pid=%d", self.name, self._proc.pid)

    def _fire_on_failure(self, reason: str) -> None:
        log.warning(
            "GROUND_LOOP|plugin=%s|event=on_failure|hook=%s|reason=%s",
            self.name, self.on_failure, reason,
        )
        if self.on_failure == "cc_recovery":
            _fire_cc_recovery(self.name, reason)

    def tick(self) -> None:
        """Poll the plugin. Call this on every loop iteration."""
        if self._breaker_tripped():
            if not self._tripped:
                log.info("GROUND_LOOP|plugin=%s|action=breaker_halt", self.name)
                self._tripped = True
            return
        self._tripped = False

        if self._is_alive():
            self._restart_count = 0
            return

        # Process is dead (or never started)
        if self._proc is not None:
            rc = self._proc.returncode
            log.warning(
                "GROUND_LOOP|plugin=%s|event=died|rc=%s|restarts=%d",
                self.name, rc, self._restart_count,
            )
            self._restart_count += 1
        else:
            log.info("GROUND_LOOP|plugin=%s|action=initial_start", self.name)

        if self._restart_count > self.max_restarts:
            self._fire_on_failure(
                f"max_restarts={self.max_restarts} exceeded (consecutive failures)"
            )
            return

        self._spawn()

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            log.info("GROUND_LOOP|plugin=%s|action=stop|pid=%d", self.name, self._proc.pid)
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()


def _fire_cc_recovery(plugin_name: str, reason: str, stderr_log: Path | None = None) -> None:
    """
    Spawn CC to diagnose and fix a repeatedly-failing plugin.

    Reads the last _STDERR_TAIL lines from the plugin's stderr log, builds a
    structured recovery prompt, and spawns CC non-blocking so Ground Loop keeps
    polling other plugins. Logs the invocation at WARNING; never raises.
    """
    # Read last N lines of plugin stderr for context
    stderr_text = ""
    log_path = stderr_log or (_STDERR_DIR / f"{plugin_name}.stderr.log")
    try:
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            stderr_text = "\n".join(lines[-_STDERR_TAIL:])
    except Exception as exc:
        log.warning("CC_RECOVERY|plugin=%s|stderr_read_failed=%s", plugin_name, exc)

    prompt = (
        f"Ground Loop reports plugin '{plugin_name}' has failed repeatedly.\n\n"
        f"Failure reason: {reason}\n\n"
        f"Last {_STDERR_TAIL} lines of plugin stderr:\n{stderr_text or '(no stderr captured)'}\n\n"
        "Task: diagnose the root cause, fix the code in "
        "~/dev/src/UnseenUniversity/, run tests "
        "(cd ~/dev/src/UnseenUniversity && .venv/bin/python3 -m pytest tests/ -x -q), "
        "commit the fix with a descriptive message, then exit cleanly. "
        "Do not restart the plugin manually — Ground Loop will restart it after you exit."
    )

    log.warning(
        "CC_RECOVERY|plugin=%s|reason=%s|action=spawning_cc",
        plugin_name, reason,
    )
    try:
        subprocess.Popen(
            ["claude", "--dangerously-skip-permissions", "-p", prompt],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.warning(
            "CC_RECOVERY|plugin=%s|action=cc_spawned",
            plugin_name,
        )
    except Exception as exc:
        log.error(
            "CC_RECOVERY|plugin=%s|action=cc_spawn_failed|exc=%s",
            plugin_name, exc,
        )

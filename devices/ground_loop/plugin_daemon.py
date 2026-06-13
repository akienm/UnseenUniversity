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

_FLAGS_DIR = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university")) / "flags"


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

    def _breaker_tripped(self) -> bool:
        return self.breaker_path.exists()

    def _is_alive(self) -> bool:
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def _spawn(self) -> None:
        env = {**os.environ, **self.start_env}
        log.info("GROUND_LOOP|plugin=%s|action=spawn|cmd=%s", self.name, self.start_cmd)
        self._proc = subprocess.Popen(
            self.start_cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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


def _fire_cc_recovery(plugin_name: str, reason: str) -> None:
    """Scaffold: log CC recovery event. Full wiring in T-ground-loop-cc-recovery."""
    log.warning(
        "CC_RECOVERY_NEEDED|plugin=%s|reason=%s|action=scaffold_only",
        plugin_name, reason,
    )

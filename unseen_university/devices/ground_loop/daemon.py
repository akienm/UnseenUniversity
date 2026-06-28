"""
GroundLoop — plugin-host process supervisor.

Scans ~/.unseen_university/ground_loop/*.yaml every poll_interval seconds.
Two plugin modes:
  - daemon:     poll + restart + on_failure hook
  - http_proxy: transparent proxy that auto-starts the backend

Fail-open: a broken plugin YAML or plugin error never crashes the loop.
Add a service: drop a YAML into ~/.unseen_university/ground_loop/.
Disable auto-start: touch ~/.unseen_university/flags/<name>.breaker

Usage:
    python3 -m unseen_university.devices.ground_loop.daemon           # run forever
    python3 -m unseen_university.devices.ground_loop.daemon --once    # one scan then exit
"""

from __future__ import annotations
from unseen_university._uu_root import uu_home

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Union

import yaml

from unseen_university.devices.skeleton.halt_registry import HaltRegistry

log = logging.getLogger(__name__)

_IGOR_HOME = Path(uu_home())
_PLUGIN_DIR = _IGOR_HOME / "ground_loop"
_FLAGS_DIR = _IGOR_HOME / "flags"
_DEFAULT_POLL = 15  # seconds

# Master-off key in the (flat-file, cold-start-safe) HaltRegistry. The ground
# loop is the lowest level of the rack — it must read its own off-switch even
# with Postgres down, so the switch is the flat-file HaltRegistry, never the DB.
# Set it (`HaltRegistry().set_halted("ground_loop", True, "<why>")`) before
# updating the ground loop / web_server / scraps / any supervised daemon so the
# loop stops restarting peers mid-update; clear it to resume.
_HALT_AGENT_ID = "ground_loop"

# Repo root: two levels up from this file (devices/ground_loop/daemon.py → repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
        cfg = yaml.safe_load(text)
        if not isinstance(cfg, dict):
            log.warning("GROUND_LOOP|yaml=%s|error=not_a_dict", path.name)
            return None
        if "name" not in cfg or "mode" not in cfg:
            log.warning("GROUND_LOOP|yaml=%s|error=missing_name_or_mode", path.name)
            return None
        return cfg
    except Exception as exc:
        log.warning("GROUND_LOOP|yaml=%s|error=load_failed|exc=%s", path.name, exc)
        return None


class GroundLoop:
    def __init__(
        self,
        poll_interval: int = _DEFAULT_POLL,
        repo_root: Path = _REPO_ROOT,
        halt_registry: HaltRegistry | None = None,
    ) -> None:
        self._poll = poll_interval
        self._daemons: dict[str, "PluginDaemon"] = {}
        self._proxies: dict[str, "PluginProxy"] = {}
        from .supervisor import RunmeSupervisor
        self._supervisor = RunmeSupervisor(repo_root)
        # Master-off switch — flat-file so the lowest-level loop reads it with the DB down.
        self._halt = halt_registry if halt_registry is not None else HaltRegistry()
        self._running = False

    def _scan_plugins(self) -> None:
        """Scan the plugin dir; add new plugins, skip ones we already manage."""
        _FLAGS_DIR.mkdir(parents=True, exist_ok=True)

        if not _PLUGIN_DIR.exists():
            return

        yamls = sorted(_PLUGIN_DIR.glob("*.yaml"))
        for ypath in yamls:
            cfg = _load_yaml(ypath)
            if not cfg:
                continue

            name = cfg["name"]
            mode = cfg["mode"]

            if name in self._daemons or name in self._proxies:
                continue  # already managing this plugin

            log.info("GROUND_LOOP|action=register|name=%s|mode=%s", name, mode)

            try:
                if mode == "daemon":
                    from .plugin_daemon import PluginDaemon
                    p = PluginDaemon(cfg)
                    self._daemons[name] = p
                elif mode == "http_proxy":
                    from .plugin_proxy import PluginProxy
                    p = PluginProxy(cfg)
                    p.start()
                    self._proxies[name] = p
                else:
                    log.warning(
                        "GROUND_LOOP|name=%s|error=unknown_mode|mode=%s", name, mode
                    )
            except Exception as exc:
                log.error(
                    "GROUND_LOOP|name=%s|action=register_failed|exc=%s", name, exc
                )

    def _tick_daemons(self) -> None:
        for name, plugin in list(self._daemons.items()):
            try:
                plugin.tick()
            except Exception as exc:
                log.error("GROUND_LOOP|name=%s|action=tick_error|exc=%s", name, exc)

    def run_once(self) -> None:
        # STUB (proof red anchor): master-off gate not yet wired — runs the full
        # cycle even when halted. Real gate restored in the next commit.
        self._scan_plugins()
        self._tick_daemons()
        self._supervisor.scan()

    def run_forever(self) -> None:
        self._running = True
        log.info("GROUND_LOOP|action=start|poll_interval=%ds|plugin_dir=%s", self._poll, _PLUGIN_DIR)

        def _handle_signal(sig, frame):
            log.info("GROUND_LOOP|action=stop_signal|sig=%s", sig)
            self._running = False

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        while self._running:
            try:
                self.run_once()
            except Exception as exc:
                log.error("GROUND_LOOP|action=loop_error|exc=%s", exc)
            time.sleep(self._poll)

        self._shutdown()

    def _shutdown(self) -> None:
        log.info("GROUND_LOOP|action=shutdown")
        for plugin in self._daemons.values():
            try:
                plugin.stop()
            except Exception as exc:
                log.error("GROUND_LOOP|action=stop_daemon_error|exc=%s", exc)
        for plugin in self._proxies.values():
            try:
                plugin.stop()
            except Exception as exc:
                log.error("GROUND_LOOP|action=stop_proxy_error|exc=%s", exc)
        self._supervisor.stop_all()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Scan once then exit")
    parser.add_argument(
        "--poll", type=int, default=_DEFAULT_POLL, help="Poll interval in seconds"
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Logging level (DEBUG/INFO/WARNING)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    gl = GroundLoop(poll_interval=args.poll)
    if args.once:
        gl.run_once()
    else:
        gl.run_forever()


if __name__ == "__main__":
    main()

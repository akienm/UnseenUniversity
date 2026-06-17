"""
supervisor.py — Ground Loop runme.py file-pattern supervisor.

Discovers all devices/*/groundloop/runme.py files in the repo, imports them
via importlib, calls start() in a daemon thread, and calls stop() on shutdown.

Plugin convention:
  devices/<name>/groundloop/runme.py  — must export start() and stop()
  devices/<name>/groundloop/config.yaml — optional launch config

Error isolation:
  - Import errors: log at ERROR, rename runme.py → runme.borkedpy
  - Runtime errors from start(): log at ERROR, rename → borkedpy
  - stop() errors: log at WARNING, continue stopping others

Hot-reload: re-scans on every tick. If a runme.py's mtime changed, stops
the running module and re-imports. Unchanged paths are skipped (O(1) per check).

Recovery: .borkedpy files untouched for 24h are logged as stale warnings.
Renaming .borkedpy back to runme.py re-enables the plugin on the next scan.

AR-009: logs every load/crash/rename at INFO/ERROR per the interface-crossing rule.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_STALE_BORKED_HOURS = 24
_STALE_BORKED_SECS = _STALE_BORKED_HOURS * 3600


class _PluginState:
    __slots__ = ("module", "thread", "mtime", "config")

    def __init__(self, module, thread: threading.Thread, mtime: float, config: dict):
        self.module = module
        self.thread = thread
        self.mtime = mtime
        self.config = config


class RunmeSupervisor:
    """Manages devices/*/groundloop/runme.py plugin modules."""

    def __init__(self, repo_root: Path) -> None:
        self._root = Path(repo_root)
        self._plugins: dict[Path, _PluginState] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def scan(self) -> None:
        """Discover and load new/changed runme.py files. Call on every tick."""
        # Check staleness of any .borkedpy files
        for borked in self._root.glob("devices/*/groundloop/runme.borkedpy"):
            self._warn_stale_borked(borked)

        # Load/reload healthy runme.py files
        for runme in sorted(self._root.glob("devices/*/groundloop/runme.py")):
            try:
                mtime = runme.stat().st_mtime
            except OSError:
                continue
            state = self._plugins.get(runme)
            if state is not None and state.mtime == mtime:
                continue  # unchanged — skip
            if state is not None:
                log.info("SUPERVISOR|runme=%s|action=hot_reload", runme.name)
                self._stop_plugin(runme)
            self._load(runme, mtime)

    def stop_all(self) -> None:
        """Stop all loaded plugins. Called on Ground Loop shutdown."""
        for path in list(self._plugins):
            self._stop_plugin(path)

    # ── internals ─────────────────────────────────────────────────────────────

    def _load(self, runme: Path, mtime: float) -> None:
        module_name = f"groundloop.{runme.parent.parent.name}"
        log.info("SUPERVISOR|runme=%s|action=load|module=%s", runme.name, module_name)
        try:
            spec = importlib.util.spec_from_file_location(module_name, runme)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as exc:
            log.error("SUPERVISOR|runme=%s|action=import_failed|exc=%s", runme.name, exc)
            self._borkedit(runme, "import_failed")
            return

        config = self._load_config(runme)

        thread = threading.Thread(
            target=self._run,
            args=(runme, mod),
            name=module_name,
            daemon=True,
        )
        thread.start()
        log.info("SUPERVISOR|runme=%s|action=started|tid=%s", runme.name, thread.name)
        self._plugins[runme] = _PluginState(
            module=mod, thread=thread, mtime=mtime, config=config
        )

    def _run(self, runme: Path, mod) -> None:
        """Thread body: call start(), catch runtime errors → borkedit."""
        try:
            mod.start()
        except Exception as exc:
            log.error("SUPERVISOR|runme=%s|action=runtime_error|exc=%s", runme.name, exc)
            self._borkedit(runme, "runtime_error")

    def _stop_plugin(self, runme: Path) -> None:
        state = self._plugins.pop(runme, None)
        if state is None:
            return
        try:
            state.module.stop()
        except Exception as exc:
            log.warning("SUPERVISOR|runme=%s|action=stop_error|exc=%s", runme.name, exc)
        log.info("SUPERVISOR|runme=%s|action=stopped", runme.name)

    def _borkedit(self, runme: Path, reason: str) -> None:
        borked = runme.with_suffix(".borkedpy")
        log.error(
            "SUPERVISOR|runme=%s|action=borkedit|reason=%s|borked=%s",
            runme.name, reason, borked.name,
        )
        try:
            runme.rename(borked)
        except Exception as exc:
            log.error(
                "SUPERVISOR|runme=%s|action=borkedit_rename_failed|exc=%s",
                runme.name, exc,
            )

    def _warn_stale_borked(self, borked: Path) -> None:
        try:
            age = time.time() - borked.stat().st_mtime
        except OSError:
            return
        if age > _STALE_BORKED_SECS:
            log.warning(
                "SUPERVISOR|borked=%s|age_hours=%.1f|action=stale_borked|"
                "hint=rename_to_runme.py_to_re_enable",
                borked.name,
                age / 3600,
            )

    @staticmethod
    def _load_config(runme: Path) -> dict:
        cfg = runme.parent / "config.yaml"
        if not cfg.exists():
            return {}
        try:
            import yaml
            return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            log.warning("SUPERVISOR|runme=%s|config_load_failed=%s", runme.name, exc)
            return {}

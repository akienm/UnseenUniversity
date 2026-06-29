"""Tests for devices/demo_daemon — Ground Loop supervisor smoke test."""

from __future__ import annotations

import os
import time
import threading
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _demo_interval(monkeypatch):
    monkeypatch.setenv("DEMO_DAEMON_INTERVAL", "1")


@pytest.fixture()
def igor_home(tmp_path, monkeypatch):
    monkeypatch.setattr("unseen_university._uu_root.uu_home", lambda: str(tmp_path))
    return tmp_path


class TestDemoDaemon:
    def _reload_module(self):
        import importlib
        import unseen_university.devices.demo_daemon.groundloop.runme as mod
        importlib.reload(mod)
        return mod

    def test_start_writes_heartbeats(self, igor_home):
        mod = self._reload_module()
        t = threading.Thread(target=mod.start, daemon=True)
        t.start()
        time.sleep(2.5)
        mod.stop()
        t.join(timeout=2)
        log = igor_home / "logs" / "demo_daemon" / "heartbeat.log"
        assert log.exists(), "heartbeat log not created"
        lines = [l for l in log.read_text().splitlines() if l.strip()]
        assert len(lines) >= 2, f"expected ≥2 heartbeats, got {len(lines)}: {lines}"

    def test_stop_unblocks_start(self, igor_home):
        mod = self._reload_module()
        started = threading.Event()
        original_start = mod.start

        def _instrumented():
            started.set()
            mod._stop_event.clear()
            original_start()

        t = threading.Thread(target=_instrumented, daemon=True)
        t.start()
        started.wait(timeout=1)
        mod.stop()
        t.join(timeout=3)
        assert not t.is_alive(), "start() did not exit after stop()"

    def test_heartbeat_log_format(self, igor_home):
        mod = self._reload_module()
        t = threading.Thread(target=mod.start, daemon=True)
        t.start()
        time.sleep(1.5)
        mod.stop()
        t.join(timeout=2)
        log = igor_home / "logs" / "demo_daemon" / "heartbeat.log"
        first_line = log.read_text().splitlines()[0]
        assert "heartbeat #1" in first_line
        assert "T" in first_line  # ISO 8601 timestamp contains T

    def test_config_yaml_exists(self):
        cfg = Path("devices/demo_daemon/groundloop/config.yaml")
        assert cfg.exists(), "config.yaml not found"
        import yaml
        data = yaml.safe_load(cfg.read_text())
        assert data.get("daemon") == "demo-daemon"
        assert "poll_interval_s" in data

    def test_supervisor_discovers_demo_daemon(self, igor_home):
        from unseen_university.devices.ground_loop.supervisor import RunmeSupervisor
        repo = Path(".")
        sup = RunmeSupervisor(repo)
        sup.scan()
        demo_runme = repo / "devices" / "demo_daemon" / "groundloop" / "runme.py"
        assert demo_runme in sup._plugins, f"demo daemon not discovered; plugins={list(sup._plugins)}"
        sup.stop_all()

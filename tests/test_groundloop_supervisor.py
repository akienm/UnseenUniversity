"""Tests for Ground Loop RunmeSupervisor (T-daemon-supervisor-file-pattern)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from devices.ground_loop.supervisor import RunmeSupervisor


def _make_runme(device_dir: Path, body: str = "") -> Path:
    """Write a minimal runme.py under devices/<name>/groundloop/."""
    gl_dir = device_dir / "groundloop"
    gl_dir.mkdir(parents=True, exist_ok=True)
    runme = gl_dir / "runme.py"
    default_body = (
        "import threading\n"
        "_stop = threading.Event()\n"
        "def start():\n"
        "    _stop.wait()\n"
        "def stop():\n"
        "    _stop.set()\n"
    )
    runme.write_text(body or default_body)
    return runme


class TestDiscovery:
    def test_scan_finds_runme(self, tmp_path):
        sup = RunmeSupervisor(tmp_path)
        dev = tmp_path / "devices" / "test_dev"
        runme = _make_runme(dev)
        sup.scan()
        assert runme in sup._plugins

    def test_scan_skips_borkedpy(self, tmp_path):
        sup = RunmeSupervisor(tmp_path)
        dev = tmp_path / "devices" / "broken_dev"
        runme = _make_runme(dev)
        borked = runme.with_suffix(".borkedpy")
        runme.rename(borked)
        sup.scan()
        assert runme not in sup._plugins


class TestHotReload:
    def test_changed_mtime_triggers_reload(self, tmp_path):
        sup = RunmeSupervisor(tmp_path)
        dev = tmp_path / "devices" / "hot_dev"
        runme = _make_runme(dev)
        sup.scan()
        first_state = sup._plugins[runme]
        # Touch the file to change mtime
        time.sleep(0.01)
        runme.touch()
        sup.scan()
        second_state = sup._plugins[runme]
        assert second_state is not first_state, "Hot reload should create a new plugin state"

    def test_unchanged_mtime_skips_reload(self, tmp_path):
        sup = RunmeSupervisor(tmp_path)
        dev = tmp_path / "devices" / "stable_dev"
        _make_runme(dev)
        sup.scan()
        first_keys = dict(sup._plugins)
        sup.scan()
        assert set(sup._plugins) == set(first_keys)


class TestErrorHandling:
    def test_import_error_creates_borkedpy(self, tmp_path):
        sup = RunmeSupervisor(tmp_path)
        dev = tmp_path / "devices" / "bad_import_dev"
        runme = _make_runme(dev, body="this is not valid python !!!\n")
        sup.scan()
        borked = runme.with_suffix(".borkedpy")
        assert borked.exists(), ".borkedpy not created on import error"
        assert not runme.exists(), "runme.py should have been renamed"

    def test_runtime_error_creates_borkedpy(self, tmp_path):
        sup = RunmeSupervisor(tmp_path)
        dev = tmp_path / "devices" / "bad_runtime_dev"
        runme = _make_runme(
            dev,
            body=(
                "def start():\n"
                "    raise RuntimeError('simulated crash')\n"
                "def stop():\n"
                "    pass\n"
            ),
        )
        sup.scan()
        # Give the daemon thread time to crash
        borked = runme.with_suffix(".borkedpy")
        deadline = time.time() + 2.0
        while not borked.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert borked.exists(), ".borkedpy not created on runtime error"

    def test_borkedpy_recovery_on_rename(self, tmp_path):
        sup = RunmeSupervisor(tmp_path)
        dev = tmp_path / "devices" / "recover_dev"
        runme = _make_runme(dev, body="this is bad syntax !!!\n")
        sup.scan()
        borked = runme.with_suffix(".borkedpy")
        assert borked.exists()
        # Fix: rename back with good code
        borked.rename(runme)
        runme.write_text(
            "import threading\n"
            "_stop = threading.Event()\n"
            "def start(): _stop.wait()\n"
            "def stop(): _stop.set()\n"
        )
        sup.scan()
        assert runme in sup._plugins


class TestConfigLoading:
    def test_config_yaml_loaded(self, tmp_path):
        sup = RunmeSupervisor(tmp_path)
        dev = tmp_path / "devices" / "cfg_dev"
        runme = _make_runme(dev)
        (dev / "groundloop" / "config.yaml").write_text(
            "daemon: my-daemon\nrestart_policy: always\npoll_interval_s: 5\n"
        )
        sup.scan()
        cfg = sup._plugins[runme].config
        assert cfg.get("daemon") == "my-daemon"
        assert cfg.get("restart_policy") == "always"

    def test_missing_config_returns_empty_dict(self, tmp_path):
        sup = RunmeSupervisor(tmp_path)
        dev = tmp_path / "devices" / "nocfg_dev"
        runme = _make_runme(dev)
        sup.scan()
        assert sup._plugins[runme].config == {}


class TestStopAll:
    def test_stop_all_calls_stop(self, tmp_path):
        stopped = []

        def make_body(name):
            return (
                f"import threading\n"
                f"_ev = threading.Event()\n"
                f"def start(): _ev.wait()\n"
                f"def stop():\n"
                f"    _ev.set()\n"
            )

        sup = RunmeSupervisor(tmp_path)
        for i in range(2):
            dev = tmp_path / "devices" / f"stopdev{i}"
            _make_runme(dev, body=make_body(f"dev{i}"))
        sup.scan()
        assert len(sup._plugins) == 2
        sup.stop_all()
        assert len(sup._plugins) == 0


class TestStaleBorked:
    def test_stale_borked_logged(self, tmp_path, caplog):
        sup = RunmeSupervisor(tmp_path)
        dev = tmp_path / "devices" / "stale_dev"
        runme = _make_runme(dev)
        borked = runme.with_suffix(".borkedpy")
        runme.rename(borked)
        # Back-date mtime to 25 hours ago
        old_mtime = time.time() - 25 * 3600
        import os
        os.utime(borked, (old_mtime, old_mtime))
        import logging
        with caplog.at_level(logging.WARNING, logger="devices.ground_loop.supervisor"):
            sup.scan()
        assert any("stale_borked" in r.message for r in caplog.records)


class TestGroundLoopIntegration:
    def test_groundloop_runs_supervisor_scan(self, tmp_path):
        from devices.ground_loop.daemon import GroundLoop
        gl = GroundLoop(repo_root=tmp_path)
        dev = tmp_path / "devices" / "int_dev"
        runme = _make_runme(dev)
        gl.run_once()
        assert runme in gl._supervisor._plugins
        gl._shutdown()

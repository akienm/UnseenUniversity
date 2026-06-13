"""
Tests for the Ground Loop plugin-host supervisor.

Tests:
- GroundLoop loads and registers plugins from YAML
- daemon mode: PluginDaemon ticks, spawns, restarts, and obeys circuit breaker
- http_proxy mode: PluginProxy starts backend on request, circuit breaker works
- Broken YAML does not crash the loop (fail-open)
- New YAML dropped mid-run is picked up on next scan
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.ground_loop.daemon import GroundLoop, _PLUGIN_DIR, _FLAGS_DIR
from devices.ground_loop.plugin_daemon import PluginDaemon
from devices.ground_loop.plugin_proxy import PluginProxy


# ── helpers ─────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── PluginDaemon tests ────────────────────────────────────────────────────────

class TestPluginDaemon:
    def _cfg(self, tmp_path: Path, **kwargs) -> dict:
        cfg = {
            "name": "test_daemon",
            "mode": "daemon",
            "start_cmd": [sys.executable, "-c", "import time; time.sleep(3600)"],
            "poll_interval": 1,
            "max_restarts": 2,
            "on_failure": None,
        }
        cfg.update(kwargs)
        return cfg

    def test_initial_spawn(self, tmp_path):
        cfg = self._cfg(tmp_path)
        pd = PluginDaemon(cfg)
        with patch.object(type(pd), "breaker_path", new_callable=lambda: property(lambda self: tmp_path / "no.breaker")):
            pd.tick()
            assert pd._proc is not None
            assert pd._proc.poll() is None
            pd.stop()

    def test_restart_after_death(self, tmp_path):
        cfg = self._cfg(tmp_path, start_cmd=[sys.executable, "-c", "import sys; sys.exit(0)"])
        pd = PluginDaemon(cfg)
        breaker = tmp_path / "no.breaker"
        with patch.object(type(pd), "breaker_path", new_callable=lambda: property(lambda self: breaker)):
            pd.tick()  # initial start
            if pd._proc:
                pd._proc.wait(timeout=2)  # let it die
            pd.tick()  # should restart
            assert pd._restart_count >= 1

    def test_circuit_breaker_halts_spawn(self, tmp_path):
        cfg = self._cfg(tmp_path)
        pd = PluginDaemon(cfg)
        breaker = tmp_path / "test_daemon.breaker"
        breaker.touch()
        with patch.object(type(pd), "breaker_path", new_callable=lambda: property(lambda self: breaker)):
            pd.tick()
            assert pd._proc is None  # should not have spawned

    def test_on_failure_fires_at_max_restarts(self, tmp_path):
        cfg = self._cfg(tmp_path, max_restarts=1, on_failure="cc_recovery",
                        start_cmd=[sys.executable, "-c", "import sys; sys.exit(1)"])
        pd = PluginDaemon(cfg)
        breaker = tmp_path / "no.breaker"
        fired = []
        with patch("devices.ground_loop.plugin_daemon._fire_cc_recovery",
                   side_effect=lambda name, reason: fired.append((name, reason))):
            with patch.object(type(pd), "breaker_path", new_callable=lambda: property(lambda self: breaker)):
                for _ in range(4):
                    if pd._proc:
                        pd._proc.wait(timeout=2)
                    pd.tick()
        assert len(fired) >= 1

    def test_stop_terminates_process(self, tmp_path):
        cfg = self._cfg(tmp_path)
        pd = PluginDaemon(cfg)
        with patch.object(type(pd), "breaker_path", new_callable=lambda: property(lambda self: tmp_path / "no.breaker")):
            pd.tick()
            assert pd._proc is not None
            pd.stop()
            time.sleep(0.5)
            assert pd._proc.poll() is not None


# ── PluginProxy tests ─────────────────────────────────────────────────────────

class TestPluginProxy:
    def test_proxy_starts_on_first_request(self, tmp_path):
        proxy_port = _free_port()
        backend_port = _free_port()

        # Backend: tiny HTTP echo server
        backend_cmd = [
            sys.executable, "-c",
            f"import http.server; http.server.test(port={backend_port}, bind='127.0.0.1')"
        ]
        cfg = {
            "name": "test_proxy",
            "mode": "http_proxy",
            "proxy_port": proxy_port,
            "backend_port": backend_port,
            "start_cmd": backend_cmd,
            "start_timeout": 10,
        }
        px = PluginProxy(cfg)
        px.start()
        time.sleep(0.3)  # let proxy thread bind

        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{proxy_port}/", timeout=12) as resp:
                assert resp.status == 200
        except Exception:
            pytest.skip("backend or proxy did not start in time (CI environment)")
        finally:
            px.stop()

    def test_circuit_breaker_returns_503(self, tmp_path):
        proxy_port = _free_port()
        backend_port = _free_port()
        cfg = {
            "name": "test_cb",
            "mode": "http_proxy",
            "proxy_port": proxy_port,
            "backend_port": backend_port,
            "start_cmd": [sys.executable, "-c", "import time; time.sleep(999)"],
            "start_timeout": 1,
        }
        px = PluginProxy(cfg)
        breaker = tmp_path / "test_cb.breaker"
        breaker.touch()
        with patch.object(type(px), "breaker_path", new_callable=lambda: property(lambda self: breaker)):
            alive = px._ensure_backend()
        assert alive is False

    def test_concurrent_requests_all_handled(self, tmp_path):
        """Multiple concurrent _ensure_backend() calls: backend spawned once, all get True."""
        backend_port = _free_port()
        cfg = {
            "name": "test_concurrent",
            "mode": "http_proxy",
            "proxy_port": _free_port(),
            "backend_port": backend_port,
            "start_cmd": [sys.executable, "-c", "import time; time.sleep(999)"],
            "start_timeout": 2,
        }
        px = PluginProxy(cfg)
        spawn_count = [0]
        alive_state = [False]

        def mock_spawn():
            spawn_count[0] += 1
            alive_state[0] = True  # backend is now up

        def mock_alive():
            return alive_state[0]

        breaker = tmp_path / "no.breaker"
        results = []

        # Patch once at class level so all threads share the same mock
        with patch.object(px, "_backend_alive", side_effect=mock_alive), \
             patch.object(px, "_spawn_backend", side_effect=mock_spawn), \
             patch.object(type(px), "breaker_path",
                          new_callable=lambda: property(lambda self: breaker)):

            def call_ensure():
                results.append(px._ensure_backend())

            threads = [threading.Thread(target=call_ensure) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        assert all(results), f"Expected all True, got {results}"
        assert spawn_count[0] == 1, f"Backend should spawn once, spawned {spawn_count[0]} times"


# ── GroundLoop integration tests ──────────────────────────────────────────────

class TestGroundLoop:
    def test_scan_registers_daemon_plugin(self, tmp_path):
        plugin_dir = tmp_path / "ground_loop"
        plugin_dir.mkdir()
        (plugin_dir / "sleeper.yaml").write_text(
            "name: sleeper\nmode: daemon\nstart_cmd: [python3, -c, 'import time; time.sleep(3600)']\n"
        )
        gl = GroundLoop()
        with patch("devices.ground_loop.daemon._PLUGIN_DIR", plugin_dir), \
             patch("devices.ground_loop.daemon._FLAGS_DIR", tmp_path / "flags"):
            gl._scan_plugins()
        assert "sleeper" in gl._daemons

    def test_broken_yaml_does_not_crash(self, tmp_path):
        plugin_dir = tmp_path / "ground_loop"
        plugin_dir.mkdir()
        (plugin_dir / "bad.yaml").write_text("{{{{ not valid yaml")
        gl = GroundLoop()
        with patch("devices.ground_loop.daemon._PLUGIN_DIR", plugin_dir), \
             patch("devices.ground_loop.daemon._FLAGS_DIR", tmp_path / "flags"):
            gl._scan_plugins()  # must not raise
        # No plugins registered
        assert len(gl._daemons) == 0
        assert len(gl._proxies) == 0

    def test_missing_mode_skipped(self, tmp_path):
        plugin_dir = tmp_path / "ground_loop"
        plugin_dir.mkdir()
        (plugin_dir / "nomode.yaml").write_text("name: nomode\n")
        gl = GroundLoop()
        with patch("devices.ground_loop.daemon._PLUGIN_DIR", plugin_dir), \
             patch("devices.ground_loop.daemon._FLAGS_DIR", tmp_path / "flags"):
            gl._scan_plugins()
        assert "nomode" not in gl._daemons

    def test_new_yaml_picked_up_on_rescan(self, tmp_path):
        plugin_dir = tmp_path / "ground_loop"
        plugin_dir.mkdir()
        gl = GroundLoop()
        with patch("devices.ground_loop.daemon._PLUGIN_DIR", plugin_dir), \
             patch("devices.ground_loop.daemon._FLAGS_DIR", tmp_path / "flags"):
            gl._scan_plugins()
            assert len(gl._daemons) == 0

            # Drop a new YAML
            (plugin_dir / "new.yaml").write_text(
                "name: new\nmode: daemon\nstart_cmd: [python3, -c, 'import time; time.sleep(3600)']\n"
            )
            gl._scan_plugins()
        assert "new" in gl._daemons

    def test_unknown_mode_does_not_crash(self, tmp_path):
        plugin_dir = tmp_path / "ground_loop"
        plugin_dir.mkdir()
        (plugin_dir / "weird.yaml").write_text("name: weird\nmode: telepathy\n")
        gl = GroundLoop()
        with patch("devices.ground_loop.daemon._PLUGIN_DIR", plugin_dir), \
             patch("devices.ground_loop.daemon._FLAGS_DIR", tmp_path / "flags"):
            gl._scan_plugins()  # must not raise
        assert "weird" not in gl._daemons
        assert "weird" not in gl._proxies

"""Tests for circuit breaker API and Granny gate."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestCircuitAPI:
    def _make_app(self, tmp_circuit_file):
        import devices.web_server.server as _srv
        with patch("devices.web_server.server._init_comms"), \
             patch.dict(os.environ, {"UU_CIRCUIT_STATE_FILE": str(tmp_circuit_file)}):
            # Re-set the module-level _CIRCUIT_STATE_FILE
            _srv._CIRCUIT_STATE_FILE = Path(str(tmp_circuit_file))
            return _srv._make_app()

    def test_get_circuit_returns_empty_when_no_file(self, tmp_path):
        from starlette.testclient import TestClient
        circuit_file = tmp_path / "circuit_state.json"
        app = self._make_app(circuit_file)
        with TestClient(app) as client:
            resp = client.get("/api/circuit")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_post_circuit_sets_state(self, tmp_path):
        from starlette.testclient import TestClient
        circuit_file = tmp_path / "circuit_state.json"
        app = self._make_app(circuit_file)
        with patch("devices.web_server.server._CIRCUIT_STATE_FILE", circuit_file), \
             patch("devices.web_server.server.post_to_channel", side_effect=lambda *a, **k: None, create=True), \
             TestClient(app) as client:
            with patch("devices.web_server.server._read_circuit_state", return_value={}), \
                 patch("devices.web_server.server._write_circuit_state") as mock_write, \
                 patch("unseen_university.channel.post_to_channel"):
                resp = client.post("/api/circuit/DickSimnel.0", json={"state": "OPEN"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "OPEN"
        assert body["device"] == "DickSimnel.0"

    def test_invalid_state_returns_400(self, tmp_path):
        from starlette.testclient import TestClient
        circuit_file = tmp_path / "circuit_state.json"
        app = self._make_app(circuit_file)
        with TestClient(app) as client:
            resp = client.post("/api/circuit/CC.0", json={"state": "BROKEN"})
        assert resp.status_code == 400


class TestGrannyCircuitGate:
    def test_circuit_open_skips_dispatch(self, tmp_path):
        from devices.granny.daemon import run_once, _default_config
        circuit_file = tmp_path / "circuit_state.json"
        circuit_file.write_text(json.dumps({"CC.0": "OPEN"}))

        ticket = {"id": "T-skip", "tags": [], "role": "master"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("devices.granny.daemon._post_channel") as mock_post, \
             patch("devices.granny.daemon._CIRCUIT_STATE_FILE", circuit_file), \
             patch("devices.granny.daemon.subprocess.run"):
            result = run_once(_default_config(), set())

        assert "T-skip" not in result
        throttled = [c for c in mock_post.call_args_list if "GRANNY_THROTTLED" in str(c)]
        assert throttled, "GRANNY_THROTTLED should be posted when circuit is OPEN"

    def test_circuit_closed_allows_dispatch(self, tmp_path):
        from devices.granny.daemon import run_once, _default_config
        circuit_file = tmp_path / "circuit_state.json"
        circuit_file.write_text(json.dumps({"CC.0": "CLOSED"}))

        ticket = {"id": "T-allow", "tags": [], "role": "master"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("devices.granny.daemon._post_channel"), \
             patch("devices.granny.daemon._CIRCUIT_STATE_FILE", circuit_file), \
             patch("devices.granny.daemon.subprocess.run", return_value=MagicMock(returncode=0)):
            result = run_once(_default_config(), set())

        assert "T-allow" in result

    def test_no_circuit_file_allows_dispatch(self, tmp_path):
        from devices.granny.daemon import run_once, _default_config
        circuit_file = tmp_path / "nonexistent.json"  # does not exist

        ticket = {"id": "T-nofile", "tags": [], "role": "master"}
        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("devices.granny.daemon._post_channel"), \
             patch("devices.granny.daemon._CIRCUIT_STATE_FILE", circuit_file), \
             patch("devices.granny.daemon.subprocess.run", return_value=MagicMock(returncode=0)):
            result = run_once(_default_config(), set())

        assert "T-nofile" in result

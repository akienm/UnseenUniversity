"""Tests for circuit breaker API and Granny gate."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestCircuitAPI:
    def _make_app(self, tmp_circuit_file):
        import unseen_university.devices.web_server.server as _srv
        with patch("unseen_university.devices.web_server.server._init_comms"), \
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
        with patch("unseen_university.devices.web_server.server._CIRCUIT_STATE_FILE", circuit_file), \
             patch("unseen_university.devices.web_server.server.post_to_channel", side_effect=lambda *a, **k: None, create=True), \
             TestClient(app) as client:
            with patch("unseen_university.devices.web_server.server._read_circuit_state", return_value={}), \
                 patch("unseen_university.devices.web_server.server._write_circuit_state") as mock_write, \
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
        from unseen_university.devices.granny.daemon import run_once, _default_config
        circuit_file = tmp_path / "circuit_state.json"
        circuit_file.write_text(json.dumps({"CC.0": "OPEN"}))

        ticket = {"id": "T-skip", "tags": [], "role": "master"}
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel") as mock_post, \
             patch("unseen_university.devices.granny.daemon._CIRCUIT_STATE_FILE", circuit_file), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", return_value=True) as mock_dispatch:
            run_once(_default_config())

        mock_dispatch.assert_not_called()
        throttled = [c for c in mock_post.call_args_list if "GRANNY_THROTTLED" in str(c)]
        assert throttled, "GRANNY_THROTTLED should be posted when circuit is OPEN"

    def test_circuit_closed_allows_dispatch(self, tmp_path):
        from unseen_university.devices.granny.daemon import run_once, _default_config
        circuit_file = tmp_path / "circuit_state.json"
        circuit_file.write_text(json.dumps({"CC.0": "CLOSED"}))

        # imap must be provided (not None) — dispatch=bus skips when imap is None
        # regardless of circuit state. MagicMock stands in for the real IMAPServer.
        fake_imap = MagicMock()
        ticket = {"id": "T-allow", "tags": [], "role": "master"}
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"), \
             patch("unseen_university.devices.granny.daemon._CIRCUIT_STATE_FILE", circuit_file), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", return_value=True) as mock_dispatch:
            run_once(_default_config(), imap=fake_imap)

        mock_dispatch.assert_called_once()
        call_ticket_id = mock_dispatch.call_args[0][0]["id"]
        assert call_ticket_id == "T-allow", f"wrong ticket dispatched: {call_ticket_id}"

    def test_no_circuit_file_allows_dispatch(self, tmp_path):
        from unseen_university.devices.granny.daemon import run_once, _default_config
        circuit_file = tmp_path / "nonexistent.json"  # does not exist

        fake_imap = MagicMock()
        ticket = {"id": "T-nofile", "tags": [], "role": "master"}
        with patch("unseen_university.devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("unseen_university.devices.granny.availability.is_available", return_value=True), \
             patch("unseen_university.devices.granny.daemon._cc0_busy", return_value=False), \
             patch("unseen_university.devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("unseen_university.devices.granny.daemon._reset_stale_inprogress", return_value=0), \
             patch("unseen_university.devices.granny.daemon._post_channel"), \
             patch("unseen_university.devices.granny.daemon._CIRCUIT_STATE_FILE", circuit_file), \
             patch("unseen_university.devices.granny.daemon._dispatch_bus", return_value=True) as mock_dispatch:
            run_once(_default_config(), imap=fake_imap)

        mock_dispatch.assert_called_once()
        call_ticket_id = mock_dispatch.call_args[0][0]["id"]
        assert call_ticket_id == "T-nofile", f"wrong ticket dispatched: {call_ticket_id}"


class TestChatSlashTicket:
    def _make_app(self):
        import unseen_university.devices.web_server.server as _srv
        with patch("unseen_university.devices.web_server.server._init_comms"):
            return _srv._make_app()

    def test_slash_ticket_creates_entry_and_returns_id(self):
        from unseen_university.devices.web_server.server import _handle_chat_slash_commands
        with patch("unseen_university.devices.web_server.server._handle_slash_ticket", return_value="Bark! Ticket filed: T-abc12345 — broken foo"):
            resp = _handle_chat_slash_commands("/ticket broken foo", "dicksimnel")
        assert resp is not None
        assert "T-abc12345" in resp

    def test_slash_ticket_empty_description_returns_usage(self):
        from unseen_university.devices.web_server.server import _handle_chat_slash_commands
        resp = _handle_chat_slash_commands("/ticket ", "dicksimnel")
        assert resp is not None
        assert "Usage" in resp

    def test_regular_message_returns_none(self):
        from unseen_university.devices.web_server.server import _handle_chat_slash_commands
        resp = _handle_chat_slash_commands("hello world", "dicksimnel")
        assert resp is None

    def test_slash_ticket_in_chat_post_intercepted(self):
        from starlette.testclient import TestClient
        app = self._make_app()
        with TestClient(app) as client, \
             patch("unseen_university.devices.web_server.server._handle_slash_ticket", return_value="Bark! Ticket filed: T-xyz99 — test problem") as mock_file:
            resp = client.post("/api/dicksimnel/chat", json={"message": "/ticket test problem"})
        assert resp.status_code == 200
        assert "T-xyz99" in resp.json()["response"]

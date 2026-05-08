"""Tests for Librarian device skeleton — T-librarian-device."""

from __future__ import annotations

import json
import time
from io import StringIO
from unittest.mock import patch

import pytest

from agent_datacenter.device import INTERFACE_VERSION
from agent_datacenter.devices.librarian import Librarian
from agent_datacenter.devices.librarian.librarian import OOK
from agent_datacenter.devices.librarian.mcp_server import _dispatch


class TestLibrarianContract:
    """BaseDevice contract: all abstract methods implemented and shaped correctly."""

    def test_who_am_i_required_keys(self):
        lib = Librarian()
        info = lib.who_am_i()
        assert info["device_id"] == "librarian"
        assert "name" in info
        assert "version" in info
        assert info["ook"] == OOK

    def test_interface_version(self):
        lib = Librarian()
        assert lib.interface_version() == INTERFACE_VERSION

    def test_requirements_has_deps(self):
        lib = Librarian()
        r = lib.requirements()
        assert "deps" in r
        assert isinstance(r["deps"], list)

    def test_capabilities_required_keys(self):
        lib = Librarian()
        c = lib.capabilities()
        assert c["can_send"] is True
        assert c["can_receive"] is True
        assert "emitted_keywords" in c
        assert isinstance(c["emitted_keywords"], list)

    def test_comms_required_keys(self):
        lib = Librarian()
        c = lib.comms()
        assert c["address"].startswith("comms://")
        assert c["mode"] in ("read_only", "write_only", "read_write")
        assert "supports_push" in c

    def test_health_returns_healthy(self):
        lib = Librarian()
        h = lib.health()
        assert h["status"] == "healthy"
        assert "checked_at" in h

    def test_uptime_increases(self):
        lib = Librarian()
        t0 = lib.uptime()
        time.sleep(0.01)
        assert lib.uptime() > t0

    def test_startup_errors_empty(self):
        lib = Librarian()
        assert lib.startup_errors() == []

    def test_logs_has_paths(self):
        lib = Librarian()
        l = lib.logs()
        assert "paths" in l
        assert isinstance(l["paths"], dict)

    def test_update_info_required_keys(self):
        lib = Librarian()
        u = lib.update_info()
        assert "current_version" in u
        assert "update_available" in u

    def test_where_and_how_required_keys(self):
        lib = Librarian()
        w = lib.where_and_how()
        assert "host" in w
        assert "pid" in w
        assert "launch_command" in w


class TestLibrarianLifecycle:
    def test_block_sets_degraded(self):
        lib = Librarian()
        lib.block("test reason")
        h = lib.health()
        assert h["status"] == "degraded"
        assert "test reason" in h["detail"]

    def test_halt_sets_unhealthy(self):
        lib = Librarian()
        lib.halt()
        assert lib.health()["status"] == "unhealthy"

    def test_restart_clears_block(self):
        lib = Librarian()
        lib.block("blocked")
        lib.restart()
        assert lib.health()["status"] == "healthy"

    def test_recovery_clears_halt(self):
        lib = Librarian()
        lib.halt()
        lib.recovery()
        assert lib.health()["status"] == "healthy"


class TestMcpServer:
    def test_initialize_returns_server_info(self):
        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        resp = _dispatch(msg)
        assert resp["id"] == 1
        assert resp["result"]["serverInfo"]["name"] == "librarian"
        assert "protocolVersion" in resp["result"]

    def test_tools_list_returns_empty(self):
        msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        resp = _dispatch(msg)
        assert resp["result"]["tools"] == []

    def test_unknown_method_returns_error(self):
        msg = {"jsonrpc": "2.0", "id": 3, "method": "unknown/method"}
        resp = _dispatch(msg)
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_notification_returns_none(self):
        msg = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert _dispatch(msg) is None

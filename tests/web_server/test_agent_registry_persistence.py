"""
test_agent_registry_persistence.py — flat-file persistence for agent registrations.

Verifies that _save_agents() writes registrations to disk and _load_agents()
restores them on the next startup, so device tabs survive a web server restart.
"""

from __future__ import annotations

import json
import threading

import pytest


@pytest.fixture(autouse=True)
def patch_registry_file(tmp_path, monkeypatch):
    import unseen_university.devices.web_server.server as srv

    monkeypatch.setattr(srv, "_AGENT_REGISTRY_FILE", tmp_path / "agent_registry.json")
    # Reset in-memory state before each test
    with srv._agents_lock:
        srv._agents.clear()
    yield
    with srv._agents_lock:
        srv._agents.clear()


def test_save_writes_json_file(tmp_path):
    import unseen_university.devices.web_server.server as srv

    with srv._agents_lock:
        srv._agents["granny"] = {
            "registered_at": "2026-06-01T10:00:00",
            "capabilities": ["routing"],
            "callback_url": "http://localhost:9001",
            "tmux_target": "igor:granny",
            "last_heartbeat": 12345.6,
        }

    srv._save_agents()

    data = json.loads(srv._AGENT_REGISTRY_FILE.read_text())
    assert "granny" in data
    assert data["granny"]["capabilities"] == ["routing"]
    # last_heartbeat is ephemeral monotonic time — must not be persisted
    assert "last_heartbeat" not in data["granny"]


def test_load_restores_registrations(tmp_path):
    import unseen_university.devices.web_server.server as srv

    registry = {
        "librarian": {
            "registered_at": "2026-06-01T09:00:00",
            "capabilities": ["memory"],
            "callback_url": "http://localhost:9002",
            "tmux_target": "igor:librarian",
        }
    }
    srv._AGENT_REGISTRY_FILE.write_text(json.dumps(registry))

    srv._load_agents()

    with srv._agents_lock:
        assert "librarian" in srv._agents
        assert srv._agents["librarian"]["capabilities"] == ["memory"]
        # last_heartbeat is None for restored agents (monotonic; no cross-boot value)
        assert srv._agents["librarian"]["last_heartbeat"] is None


def test_load_missing_file_is_silent():
    import unseen_university.devices.web_server.server as srv

    # File doesn't exist — should not raise
    srv._load_agents()

    with srv._agents_lock:
        assert srv._agents == {}


def test_round_trip(tmp_path):
    import unseen_university.devices.web_server.server as srv

    with srv._agents_lock:
        srv._agents["granny"] = {
            "registered_at": "2026-06-01T10:00:00",
            "capabilities": ["routing", "escalation"],
            "callback_url": "http://localhost:9001",
            "tmux_target": "igor:granny",
            "last_heartbeat": 99999.0,
        }

    srv._save_agents()

    # Simulate restart: clear in-memory state
    with srv._agents_lock:
        srv._agents.clear()

    srv._load_agents()

    with srv._agents_lock:
        assert "granny" in srv._agents
        assert srv._agents["granny"]["registered_at"] == "2026-06-01T10:00:00"
        assert "last_heartbeat" not in json.loads(
            srv._AGENT_REGISTRY_FILE.read_text()
        ).get("granny", {})


def test_deregister_removes_from_file(tmp_path):
    """After deregister, agent should not appear in the persisted file."""
    import unseen_university.devices.web_server.server as srv

    with srv._agents_lock:
        srv._agents["granny"] = {
            "registered_at": "2026-06-01T10:00:00",
            "capabilities": [],
            "callback_url": "",
            "tmux_target": "",
            "last_heartbeat": 1.0,
        }

    srv._save_agents()

    with srv._agents_lock:
        srv._agents.pop("granny", None)

    srv._save_agents()

    data = json.loads(srv._AGENT_REGISTRY_FILE.read_text())
    assert "granny" not in data

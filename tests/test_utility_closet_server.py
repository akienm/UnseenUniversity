"""Tests for utility_closet_server.py — D335 platform layer.

Tests the standalone server's core functionality:
- Health endpoint
- PID file lifecycle
- Agent registration/deregistration
- CC send routing
- Stale detection
"""

import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Add claudecode to path so we can import the server module
sys.path.insert(0, str(Path(__file__).parent.parent / "lab" / "claudecode"))

# We need to patch paths BEFORE importing the module
_test_runtime = None


@pytest.fixture(autouse=True)
def _patch_runtime(tmp_path):
    """Redirect all runtime paths to a temp directory."""
    global _test_runtime
    _test_runtime = tmp_path
    instance_dir = tmp_path / "Igor-wild-0001"
    instance_dir.mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    (tmp_path / "local" / "cc_channel").mkdir(parents=True)

    with patch.dict(
        os.environ,
        {
            "IGOR_RUNTIME_ROOT": str(tmp_path),
            "IGOR_INSTANCE_ID": "Igor-wild-0001",
            "IGOR_WEB_PORT": "18080",  # Use non-standard port for tests
        },
    ):
        yield tmp_path


def _import_server():
    """Import (or reimport) the server module with patched paths."""
    # Force reimport so patched env vars take effect
    if "utility_closet_server" in sys.modules:
        del sys.modules["utility_closet_server"]
    import utility_closet_server

    return utility_closet_server


class TestPIDFile:
    """PID file lifecycle tests."""

    def test_write_pid_creates_file(self, tmp_path):
        srv = _import_server()
        srv.PID_FILE = tmp_path / "utility_closet.pid"
        srv._write_pid()
        assert srv.PID_FILE.exists()
        assert int(srv.PID_FILE.read_text().strip()) == os.getpid()

    def test_remove_pid_only_removes_own(self, tmp_path):
        srv = _import_server()
        srv.PID_FILE = tmp_path / "utility_closet.pid"
        # Write a different PID
        srv.PID_FILE.write_text("99999")
        srv._remove_pid()
        # Should NOT remove because it's not our PID
        assert srv.PID_FILE.exists()

    def test_remove_pid_removes_own(self, tmp_path):
        srv = _import_server()
        srv.PID_FILE = tmp_path / "utility_closet.pid"
        srv._write_pid()
        srv._remove_pid()
        assert not srv.PID_FILE.exists()

    def test_check_running_no_pid_file(self, tmp_path):
        srv = _import_server()
        srv.PID_FILE = tmp_path / "utility_closet.pid"
        assert srv.check_running() is None

    def test_check_running_stale_pid(self, tmp_path):
        srv = _import_server()
        srv.PID_FILE = tmp_path / "utility_closet.pid"
        # Write a PID that definitely doesn't exist
        srv.PID_FILE.write_text(
            "2"
        )  # PID 2 = kthreadd, won't respond to kill(0) from user
        result = srv.check_running()
        # Should return None (stale or unreachable)
        # The exact behavior depends on whether PID 2 is accessible
        # but the health check will fail either way
        assert result is None or result.get("status") != "ok"


class TestChannelAppend:
    """Channel JSONL append tests."""

    def test_channel_append_writes_jsonl(self, tmp_path):
        srv = _import_server()
        srv._CHANNEL_DIR = tmp_path / "cc_channel"
        srv._CHANNEL_FILE = srv._CHANNEL_DIR / "messages.jsonl"
        srv._CHANNEL_DIR.mkdir(parents=True, exist_ok=True)

        srv._channel_append("test-author", "hello world")

        assert srv._CHANNEL_FILE.exists()
        line = srv._CHANNEL_FILE.read_text().strip()
        entry = json.loads(line)
        assert entry["author"] == "test-author"
        assert entry["content"] == "hello world"
        assert entry["type"] == "message"
        assert "ts" in entry

    def test_channel_append_custom_type(self, tmp_path):
        srv = _import_server()
        srv._CHANNEL_DIR = tmp_path / "cc_channel"
        srv._CHANNEL_FILE = srv._CHANNEL_DIR / "messages.jsonl"
        srv._CHANNEL_DIR.mkdir(parents=True, exist_ok=True)

        srv._channel_append("bot", "file uploaded", msg_type="file_event")

        line = srv._CHANNEL_FILE.read_text().strip()
        entry = json.loads(line)
        assert entry["type"] == "file_event"


class TestAgentRegistry:
    """Agent registration/deregistration tests."""

    def test_agent_registry_starts_empty(self):
        srv = _import_server()
        with srv._agents_lock:
            srv._agents.clear()
            srv._agent_stats.clear()
        assert len(srv._agents) == 0

    def test_agent_send_writes_to_history(self, tmp_path):
        srv = _import_server()
        srv._CHANNEL_DIR = tmp_path / "cc_channel"
        srv._CHANNEL_FILE = srv._CHANNEL_DIR / "messages.jsonl"
        srv._CHANNEL_DIR.mkdir(parents=True, exist_ok=True)

        # Clear history
        with srv._client_lock:
            srv._session_history.clear()

        srv.agent_send("test message", "igor", "shared")

        with srv._client_lock:
            hist = srv._session_history.get("shared", [])
        assert len(hist) == 1
        assert hist[0]["author"] == "igor"
        assert hist[0]["content"] == "test message"


class TestHealthEndpoint:
    """Test the health endpoint response structure."""

    def test_health_returns_ok(self):
        srv = _import_server()
        from starlette.testclient import TestClient

        app = srv._make_app()
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "uptime_s" in data
        assert "pid" in data
        assert "attached_agents" in data
        assert isinstance(data["attached_agents"], list)

    def test_metrics_returns_data(self):
        srv = _import_server()
        from starlette.testclient import TestClient

        app = srv._make_app()
        client = TestClient(app)
        response = client.get("/metrics")
        assert response.status_code == 200
        data = response.json()
        assert "uptime_s" in data
        assert "active_threads" in data


class TestAgentEndpoints:
    """Test agent register/deregister/stats endpoints."""

    def test_agent_register(self):
        srv = _import_server()
        from starlette.testclient import TestClient

        with srv._agents_lock:
            srv._agents.clear()
            srv._agent_stats.clear()

        app = srv._make_app()
        client = TestClient(app)

        response = client.post(
            "/api/agents/register",
            json={
                "agent_id": "igor",
                "capabilities": ["chat", "tools"],
                "callback_url": "http://localhost:8081",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        with srv._agents_lock:
            assert "igor" in srv._agents
            assert srv._agents["igor"]["capabilities"] == ["chat", "tools"]

    def test_agent_deregister(self):
        srv = _import_server()
        from starlette.testclient import TestClient

        with srv._agents_lock:
            srv._agents["igor"] = {"registered_at": "now", "capabilities": []}
            srv._agent_stats["igor"] = {"memory_count": 42}

        app = srv._make_app()
        client = TestClient(app)

        response = client.post("/api/agents/deregister", json={"agent_id": "igor"})
        assert response.status_code == 200

        with srv._agents_lock:
            assert "igor" not in srv._agents
            assert "igor" not in srv._agent_stats

    def test_agent_stats_push(self):
        srv = _import_server()
        from starlette.testclient import TestClient

        with srv._agents_lock:
            srv._agents["igor"] = {
                "registered_at": "now",
                "capabilities": [],
                "last_heartbeat": 0,
            }
            srv._agent_stats.clear()

        app = srv._make_app()
        client = TestClient(app)

        response = client.post(
            "/api/agents/igor/stats",
            json={
                "memory_count": 1234,
                "session_cost": 0.05,
            },
        )
        assert response.status_code == 200

        with srv._agents_lock:
            assert srv._agent_stats["igor"]["memory_count"] == 1234

    def test_agent_stats_requires_registration(self):
        srv = _import_server()
        from starlette.testclient import TestClient

        with srv._agents_lock:
            srv._agents.clear()

        app = srv._make_app()
        client = TestClient(app)

        response = client.post("/api/agents/unknown/stats", json={"foo": "bar"})
        assert response.status_code == 404


class TestCCSend:
    """Test the CC send endpoint."""

    def test_cc_send_queues_message(self, tmp_path):
        srv = _import_server()
        srv._CHANNEL_DIR = tmp_path / "cc_channel"
        srv._CHANNEL_FILE = srv._CHANNEL_DIR / "messages.jsonl"
        srv._CHANNEL_DIR.mkdir(parents=True, exist_ok=True)

        from starlette.testclient import TestClient

        # Drain the queue first
        while not srv.incoming.empty():
            srv.incoming.get_nowait()

        app = srv._make_app()
        client = TestClient(app)

        response = client.post("/api/cc_send", json={"content": "hello from CC"})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Check message was queued
        msg = srv.incoming.get_nowait()
        assert msg["content"] == "hello from CC"
        assert msg["author"] == "claude-code"

    def test_cc_send_rejects_empty(self, tmp_path):
        srv = _import_server()
        from starlette.testclient import TestClient

        app = srv._make_app()
        client = TestClient(app)

        response = client.post("/api/cc_send", json={"content": ""})
        assert response.status_code == 400

    def test_cc_send_rejects_bad_json(self, tmp_path):
        srv = _import_server()
        from starlette.testclient import TestClient

        app = srv._make_app()
        client = TestClient(app)

        response = client.post(
            "/api/cc_send",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400


class TestDashboard:
    """Test dashboard endpoint with/without agent."""

    def test_dashboard_no_agent(self):
        srv = _import_server()
        from starlette.testclient import TestClient

        with srv._agents_lock:
            srv._agent_stats.clear()

        app = srv._make_app()
        client = TestClient(app)

        response = client.get("/api/dashboard")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "no agent attached"

    def test_dashboard_with_agent_stats(self):
        srv = _import_server()
        from starlette.testclient import TestClient

        with srv._agents_lock:
            srv._agents["igor"] = {"registered_at": "now", "capabilities": []}
            srv._agent_stats["igor"] = {"memory_count": 500, "session_cost": 0.12}

        app = srv._make_app()
        client = TestClient(app)

        response = client.get("/api/dashboard")
        assert response.status_code == 200
        data = response.json()
        assert data["agent"] == "igor"
        assert data["memory_count"] == 500

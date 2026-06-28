"""
tests/web_server/test_device_controls.py — /api/devices + /devices toggle endpoint tests.

Tests:
- GET /api/devices returns all 3 shim slots; defaults to CLOSED when file absent
- GET /api/devices reflects OPEN state from circuit_state.json
- GET /api/devices announced=True when ~/.granny/announced/<id>.json exists
- GET /api/devices available=True when .true file present and no .false file
- GET /api/devices available=False when .false file wins over .true
- POST /api/devices/CC.0/toggle flips CLOSED → OPEN in circuit_state.json
- POST /api/devices/CC.0/toggle flips OPEN → CLOSED
- POST /api/devices/UNKNOWN/toggle returns 404
- POST /api/devices/DS.0/toggle uses atomic write (os.replace called)
- GET /devices returns HTML page with slot names
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_app(tmp_circuit_file: Path, granny_home: Path):
    """Build a test Starlette app with isolated circuit file + granny home."""
    import unseen_university.devices.web_server.server as srv
    with patch("unseen_university.devices.web_server.server._init_comms"):
        srv._CIRCUIT_STATE_FILE = tmp_circuit_file
        srv._GRANNY_HOME = granny_home
        return srv._make_app()


# ── GET /api/devices ──────────────────────────────────────────────────────────


def test_devices_list_defaults_closed_when_no_file(tmp_path):
    from starlette.testclient import TestClient
    circuit_file = tmp_path / "circuit_state.json"
    granny_home = tmp_path / ".granny"
    app = _make_app(circuit_file, granny_home)
    with TestClient(app) as client:
        resp = client.get("/api/devices")
    assert resp.status_code == 200
    data = resp.json()
    slots = {s["worker_id"]: s for s in data["slots"]}
    assert set(slots.keys()) == {"CC.0", "CC.1", "DS.0"}
    for slot in slots.values():
        assert slot["circuit"] == "CLOSED"
        assert slot["announced"] is False
        assert slot["available"] is False


def test_devices_list_reflects_open_circuit(tmp_path):
    from starlette.testclient import TestClient
    circuit_file = tmp_path / "circuit_state.json"
    circuit_file.write_text(json.dumps({"CC.0": "OPEN"}))
    granny_home = tmp_path / ".granny"
    app = _make_app(circuit_file, granny_home)
    with TestClient(app) as client:
        resp = client.get("/api/devices")
    assert resp.status_code == 200
    data = resp.json()
    slots = {s["worker_id"]: s for s in data["slots"]}
    assert slots["CC.0"]["circuit"] == "OPEN"
    assert slots["CC.1"]["circuit"] == "CLOSED"


def test_devices_list_announced_true_when_file_exists(tmp_path):
    from starlette.testclient import TestClient
    circuit_file = tmp_path / "circuit_state.json"
    granny_home = tmp_path / ".granny"
    announced_dir = granny_home / "announced"
    announced_dir.mkdir(parents=True)
    (announced_dir / "CC.1.json").write_text("{}")
    app = _make_app(circuit_file, granny_home)
    with TestClient(app) as client:
        resp = client.get("/api/devices")
    slots = {s["worker_id"]: s for s in resp.json()["slots"]}
    assert slots["CC.1"]["announced"] is True
    assert slots["CC.0"]["announced"] is False


def test_devices_list_available_true_when_true_file_only(tmp_path):
    from starlette.testclient import TestClient
    circuit_file = tmp_path / "circuit_state.json"
    granny_home = tmp_path / ".granny"
    avail_dir = granny_home / "available"
    avail_dir.mkdir(parents=True)
    (avail_dir / "DS.0.available.true").touch()
    app = _make_app(circuit_file, granny_home)
    with TestClient(app) as client:
        resp = client.get("/api/devices")
    slots = {s["worker_id"]: s for s in resp.json()["slots"]}
    assert slots["DS.0"]["available"] is True


def test_devices_list_available_false_when_false_file_wins(tmp_path):
    from starlette.testclient import TestClient
    circuit_file = tmp_path / "circuit_state.json"
    granny_home = tmp_path / ".granny"
    avail_dir = granny_home / "available"
    avail_dir.mkdir(parents=True)
    (avail_dir / "CC.0.available.true").touch()
    (avail_dir / "CC.0.available.false").touch()
    app = _make_app(circuit_file, granny_home)
    with TestClient(app) as client:
        resp = client.get("/api/devices")
    slots = {s["worker_id"]: s for s in resp.json()["slots"]}
    assert slots["CC.0"]["available"] is False


# ── POST /api/devices/{worker_id}/toggle ──────────────────────────────────────


def test_toggle_closed_to_open(tmp_path):
    from starlette.testclient import TestClient
    circuit_file = tmp_path / "circuit_state.json"
    granny_home = tmp_path / ".granny"
    app = _make_app(circuit_file, granny_home)
    with patch("unseen_university.channel.post_to_channel", create=True):
        with TestClient(app) as client:
            resp = client.post("/api/devices/CC.0/toggle")
    assert resp.status_code == 200
    body = resp.json()
    assert body["previous"] == "CLOSED"
    assert body["circuit"] == "OPEN"
    assert body["worker_id"] == "CC.0"
    assert json.loads(circuit_file.read_text())["CC.0"] == "OPEN"


def test_toggle_open_to_closed(tmp_path):
    from starlette.testclient import TestClient
    circuit_file = tmp_path / "circuit_state.json"
    circuit_file.write_text(json.dumps({"CC.0": "OPEN"}))
    granny_home = tmp_path / ".granny"
    app = _make_app(circuit_file, granny_home)
    with patch("unseen_university.channel.post_to_channel", create=True):
        with TestClient(app) as client:
            resp = client.post("/api/devices/CC.0/toggle")
    assert resp.status_code == 200
    assert resp.json()["circuit"] == "CLOSED"
    assert json.loads(circuit_file.read_text())["CC.0"] == "CLOSED"


def test_toggle_unknown_slot_returns_404(tmp_path):
    from starlette.testclient import TestClient
    circuit_file = tmp_path / "circuit_state.json"
    granny_home = tmp_path / ".granny"
    app = _make_app(circuit_file, granny_home)
    with TestClient(app) as client:
        resp = client.post("/api/devices/UNKNOWN/toggle")
    assert resp.status_code == 404


def test_toggle_uses_atomic_write(tmp_path):
    """os.replace() is called (not write_text) — verifies tmp+rename path."""
    from starlette.testclient import TestClient
    circuit_file = tmp_path / "circuit_state.json"
    granny_home = tmp_path / ".granny"
    app = _make_app(circuit_file, granny_home)
    with patch("unseen_university.devices.web_server.server.os.replace") as mock_replace, \
         patch("unseen_university.channel.post_to_channel", create=True):
        with TestClient(app) as client:
            resp = client.post("/api/devices/DS.0/toggle")
    assert resp.status_code == 200
    mock_replace.assert_called_once()


# ── GET /devices (HTML page) ──────────────────────────────────────────────────


def test_devices_page_returns_html(tmp_path):
    from starlette.testclient import TestClient
    circuit_file = tmp_path / "circuit_state.json"
    granny_home = tmp_path / ".granny"
    app = _make_app(circuit_file, granny_home)
    with TestClient(app) as client:
        resp = client.get("/devices")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "CC.0" in resp.text or "Dispatch Slots" in resp.text

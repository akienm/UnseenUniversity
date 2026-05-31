"""
Smoke test: web_server GET /api/health endpoint.

Spins up the Starlette app in-process via TestClient and verifies that the
health endpoint returns 200 with a JSON body that contains a 'status' key.
No uvicorn or real network port is required.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from devices.web_server.server import _make_app

    app = _make_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_api_health_returns_200(client):
    response = client.get("/api/health")
    assert response.status_code == 200


def test_api_health_body_is_json_with_status_key(client):
    response = client.get("/api/health")
    data = response.json()
    assert "status" in data


def test_api_health_status_value_is_ok(client):
    response = client.get("/api/health")
    assert response.json()["status"] == "ok"


def test_health_alias_also_returns_200(client):
    """Both /health and /api/health are routed to the same handler."""
    response = client.get("/health")
    assert response.status_code == 200
    assert "status" in response.json()


def test_api_rack_health_returns_200(client):
    response = client.get("/api/rack/health")
    assert response.status_code == 200


def test_api_rack_health_required_keys(client):
    data = client.get("/api/rack/health").json()
    for key in ("web_server", "devices", "machines", "budget", "local_hostname", "ts"):
        assert key in data, f"missing key: {key}"


def test_api_rack_health_devices_is_list(client):
    data = client.get("/api/rack/health").json()
    assert isinstance(data["devices"], list)


def test_api_rack_health_machines_is_list(client):
    data = client.get("/api/rack/health").json()
    assert isinstance(data["machines"], list)


def test_api_rack_health_web_server_has_uptime(client):
    ws = client.get("/api/rack/health").json()["web_server"]
    assert "uptime_s" in ws
    assert ws["uptime_s"] >= 0


def test_rack_page_returns_200(client):
    response = client.get("/rack")
    assert response.status_code == 200


def test_rack_page_contains_devices_section(client):
    html = client.get("/rack").text
    assert "Rack Devices" in html


def test_rack_page_contains_machines_section(client):
    html = client.get("/rack").text
    assert "Machines" in html

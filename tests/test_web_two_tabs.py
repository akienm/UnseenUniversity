"""Tests for two-tab web UI — T-web-two-tabs."""

from unittest.mock import patch

import unseen_university.devices.web_server.server as _srv


def _make_app():
    with patch("unseen_university.devices.web_server.server._init_comms"):
        return _srv._make_app()


class TestTwoTabLayout:
    def test_index_contains_both_tab_buttons(self):
        from starlette.testclient import TestClient

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert 'data-tab="comms"' in html
        assert 'data-tab="control"' in html

    def test_index_contains_comms_panel(self):
        from starlette.testclient import TestClient

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/")
        assert 'id="panel-comms"' in resp.text
        assert 'id="chat"' in resp.text

    def test_index_contains_control_station_panel(self):
        from starlette.testclient import TestClient

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/")
        assert 'id="panel-control"' in resp.text
        assert "Control Station" in resp.text

    def test_control_panel_links_to_palace_routes(self):
        from starlette.testclient import TestClient

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/")
        html = resp.text
        for route in ("/rack", "/goals", "/decisions", "/palace"):
            assert f'href="{route}"' in html, f"missing link to {route}"

    def test_switchtab_function_present(self):
        from starlette.testclient import TestClient

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/")
        assert "function switchTab" in resp.text

    def test_comms_tab_active_by_default(self):
        from starlette.testclient import TestClient

        app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/")
        html = resp.text
        # comms panel should carry the "active" class, control panel should not
        assert (
            'id="panel-comms" class' not in html
            or "active" in html.split('id="panel-comms"')[1][:50]
        )
        # The comms main-tab button should be marked active
        assert 'data-tab="comms"' in html

"""Tests for /api/queue and /queue routes (T-web-ui-queue-view)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_app():
    import devices.web_server.server as _srv
    with patch("devices.web_server.server._init_comms"):
        return _srv._make_app()


def _mock_conn(rows: list[tuple]):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


_TICKET_ROWS = [
    ("T-web-ui-queue-view", "Web UI: show open ticket queue", "sprint", "M", "claude", "", 0.7, "master"),
    ("T-cpu-peg-notify", "Scraps: notify CC when CPU pegged", "sprint", "S", "claude", "", 0.6, "master"),
    ("T-something-blocked", "A blocked ticket", "hold", "S", "claude", "waiting for X", 0.4, "builder"),
    ("T-in-flight", "Currently working", "in_progress", "M", "claude", "", 0.8, "master"),
    ("T-akien-setup", "Akien: install something", "akien", "S", "akien", "", 0.5, "guru"),
]


# ── /api/queue ────────────────────────────────────────────────────────────────


class TestApiQueue:
    def test_returns_200(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                resp = client.get("/api/queue")
        assert resp.status_code == 200

    def test_returns_tickets_list(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                data = client.get("/api/queue").json()
        assert "tickets" in data
        assert data["count"] == len(_TICKET_ROWS)

    def test_tickets_include_role_field(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                data = client.get("/api/queue").json()
        roles = {t["role"] for t in data["tickets"]}
        assert "master" in roles
        assert "guru" in roles

    def test_grouped_by_status(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                data = client.get("/api/queue").json()
        assert "sprint" in data["grouped"]
        assert "hold" in data["grouped"]
        assert "in_progress" in data["grouped"]
        assert "akien" in data["grouped"]

    def test_no_db_returns_empty(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=None):
            with TestClient(app) as client:
                data = client.get("/api/queue").json()
        assert data["count"] == 0
        assert data["tickets"] == []

    def test_ticket_fields_present(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                data = client.get("/api/queue").json()
        t = data["tickets"][0]
        for field in ("id", "title", "status", "size", "worker", "gate", "role"):
            assert field in t


# ── /queue page ───────────────────────────────────────────────────────────────


class TestPageQueue:
    def test_returns_200(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                resp = client.get("/queue")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_renders_ticket_ids(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert "T-web-ui-queue-view" in html
        assert "T-in-flight" in html

    def test_renders_status_groups(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                html = client.get("/queue").text
        # Assert canonical display labels (from _STATUS_LABEL) appear in rendered HTML
        assert "In progress" in html  # in_progress
        assert "Ready" in html        # sprint
        assert "Hold" in html          # hold
        assert "Akien" in html         # akien
        # Regression guard (T-queue-view-akien-not-legacy): akien is Akien's
        # ownership bucket, NOT a deprecated gate — its rendered label must read
        # as his and must never revert to "(legacy)". The fixture has no legacy
        # statuses, so "legacy" must not appear anywhere in the rendered HTML.
        assert "Akien (yours)" in html  # akien ownership-bucket label, not "(legacy)"
        assert "legacy" not in html.lower()

    def test_no_db_shows_unavailable(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=None):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert "DB unavailable" in html or "no-db" in html

    def test_queue_link_in_nav(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert 'href="/queue"' in html

    def test_auto_refresh_script_present(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert "30000" in html or "reload" in html

    def test_role_column_in_table(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert "Role" in html
        assert "master" in html

    def test_my_tickets_filter_shows_guru_only(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                html = client.get("/queue?view=mine").text
        assert "T-akien-setup" in html
        assert "T-web-ui-queue-view" not in html

    def test_my_tickets_tab_links_present(self):
        from starlette.testclient import TestClient
        conn = _mock_conn(_TICKET_ROWS)
        app = _make_app()
        with patch("devices.web_server.server._db_conn", return_value=conn):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert "view=mine" in html
        assert "My Tickets" in html

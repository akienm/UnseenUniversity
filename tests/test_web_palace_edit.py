"""Tests for palace edit routes — T-web-palace-edit."""

import os
from unittest.mock import patch

import devices.web_server.server as _srv


def _make_app():
    with patch("devices.web_server.server._init_comms"):
        return _srv._make_app()


class TestPalaceEditDisabled:
    def test_get_edit_returns_403_when_token_not_set(self):
        from starlette.testclient import TestClient

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADC_EDIT_TOKEN", None)
            app = _make_app()
        with TestClient(app) as client:
            resp = client.get("/palace-edit/palace.shared.akien.goals")
        assert resp.status_code == 403

    def test_post_edit_returns_403_when_token_not_set(self):
        from starlette.testclient import TestClient

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADC_EDIT_TOKEN", None)
            app = _make_app()
        with TestClient(app) as client:
            resp = client.post(
                "/palace-edit/palace.shared.akien.goals",
                data={"title": "t", "content": "c", "_token": "anything"},
            )
        assert resp.status_code == 403


class TestPalaceEditAuth:
    def test_post_with_wrong_token_returns_403(self):
        from starlette.testclient import TestClient

        with patch.dict(os.environ, {"ADC_EDIT_TOKEN": "secret123"}):
            app = _make_app()
            with TestClient(app) as client:
                resp = client.post(
                    "/palace-edit/palace.some.node",
                    data={"title": "t", "content": "c", "_token": "wrongtoken"},
                )
        assert resp.status_code == 403

    def test_post_with_correct_token_attempts_db_upsert(self):
        """With correct token and no DB, returns 503 (not 403)."""
        from starlette.testclient import TestClient

        with patch.dict(os.environ, {"ADC_EDIT_TOKEN": "secret123"}):
            os.environ.pop("UU_HOME_DB_URL", None)
            app = _make_app()
            with TestClient(app) as client:
                resp = client.post(
                    "/palace-edit/palace.some.node",
                    data={"title": "t", "content": "c", "_token": "secret123"},
                )
        # No DB configured → 503 (not 403 — token was valid)
        assert resp.status_code == 503

    def test_get_edit_form_shows_form_when_token_configured(self):
        """GET /palace-edit/{path} returns a form when ADC_EDIT_TOKEN is set."""
        from starlette.testclient import TestClient

        mock_conn = _fake_db_conn_with_row(
            ("palace.some.node", "Test Node", "node content here")
        )
        with patch.dict(os.environ, {"ADC_EDIT_TOKEN": "secret123"}):
            with patch("devices.web_server.server._db_conn", return_value=mock_conn):
                app = _make_app()
                with TestClient(app) as client:
                    resp = client.get("/palace-edit/palace.some.node")
        assert resp.status_code == 200
        assert b"<form" in resp.content
        assert b"node content here" in resp.content


def _fake_db_conn_with_row(row):
    """Return a minimal mock psycopg2 connection that yields one row."""
    from unittest.mock import MagicMock

    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = row

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_conn.close = MagicMock()
    return mock_conn

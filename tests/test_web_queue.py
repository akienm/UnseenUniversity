"""Tests for /api/queue and /queue routes (T-web-ui-queue-view)."""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch


# ── Status-label single-source consolidation (T-status-label-source-consolidation) ──


def test_status_label_single_canonical_source():
    """Both render paths import the SAME status-label dict object — no drift."""
    import importlib

    import os
    import sys

    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "devlab", "claudecode")
    )
    canonical = importlib.import_module("unseen_university.ticket_status")
    server = importlib.import_module("unseen_university.devices.web_server.server")
    queue_view = importlib.import_module("queue_view")

    # Same object identity, not just equal values — a change can't diverge.
    assert server._STATUS_LABEL is canonical.STATUS_LABEL
    assert queue_view._STATUS_LABEL is canonical.STATUS_LABEL
    assert server._STATUS_ORDER is canonical.STATUS_ORDER
    assert queue_view._STATUS_ORDER is canonical.STATUS_ORDER


def test_queue_view_runs_as_bare_script_under_system_python():
    """The /mytickets + /opentickets skills run `python3 queue_view.py` as a bare
    file under the SYSTEM python3 (not the venv). For a script file sys.path[0] is
    the script's own dir, so the top-level `from unseen_university.ticket_status
    import ...` would raise ModuleNotFoundError unless the script bootstraps the
    repo root onto sys.path. Reproduce that exact invocation from a neutral cwd so
    the consolidation import can never silently break the skills again.
    """
    import os
    import subprocess
    import sys

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    script = os.path.join(repo_root, "devlab", "claudecode", "queue_view.py")
    # Run from /tmp with a clean PYTHONPATH so only the script's own bootstrap can
    # make unseen_university importable — mirrors the skill's bare invocation.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, script, "--view", "opentickets"],
        cwd="/tmp",
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # Must not crash on import. (Exit may be non-zero only if the DB is down; the
    # import error we're guarding against shows as ModuleNotFoundError in stderr.)
    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr
    assert "No module named 'unseen_university'" not in proc.stderr, proc.stderr


def test_akien_is_not_legacy_anywhere():
    """The akien bucket must render as Akien's, never marked '(legacy)'."""
    from unseen_university.ticket_status import STATUS_LABEL

    assert "Akien" in STATUS_LABEL["akien"]
    assert "legacy" not in STATUS_LABEL["akien"].lower()
    # No canonical (non-legacy) status carries a legacy marker.
    for status in ("in_progress", "sprint", "triage", "dependency", "hold", "akien"):
        assert "legacy" not in STATUS_LABEL[status].lower(), status


def _make_app():
    import unseen_university.devices.web_server.server as _srv
    with patch("unseen_university.devices.web_server.server._init_comms"):
        return _srv._make_app()


_TICKET_ROWS = [
    ("T-web-ui-queue-view", "Web UI: show open ticket queue", "sprint", "M", "claude", "", 0.7, "master"),
    ("T-cpu-peg-notify", "Scraps: notify CC when CPU pegged", "sprint", "S", "claude", "", 0.6, "master"),
    ("T-something-blocked", "A blocked ticket", "hold", "S", "claude", "waiting for X", 0.4, "builder"),
    ("T-in-flight", "Currently working", "in_progress", "M", "claude", "", 0.8, "master"),
    ("T-akien-setup", "Akien: install something", "akien", "S", "akien", "", 0.5, "guru"),
]

_BODY_KEYS = ("id", "title", "status", "size", "worker", "gate", "priority", "role")


def _rows_to_bodies(rows: list[tuple]) -> list[dict]:
    return [dict(zip(_BODY_KEYS, r)) for r in rows]


@contextlib.contextmanager
def _serving(rows: list[tuple]):
    """Serve fixture tickets from the filesystem store the queue routes now read.

    The /queue page still calls _db_conn() for its generic 'DB unavailable'
    banner, so we hand it a truthy conn; the ticket data itself comes from
    ticket_store.list (D-build-queue-filesystem-first).
    """
    bodies = _rows_to_bodies(rows)
    conn = MagicMock()  # truthy — clears the page's _db_conn banner check
    with (
        patch("unseen_university.devices.web_server.server._db_conn", return_value=conn),
        patch("unseen_university.ticket_store.list", return_value=bodies),
    ):
        yield


# ── /api/queue ────────────────────────────────────────────────────────────────


class TestApiQueue:
    def test_returns_200(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                resp = client.get("/api/queue")
        assert resp.status_code == 200

    def test_returns_tickets_list(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                data = client.get("/api/queue").json()
        assert "tickets" in data
        assert data["count"] == len(_TICKET_ROWS)

    def test_tickets_include_role_field(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                data = client.get("/api/queue").json()
        roles = {t["role"] for t in data["tickets"]}
        assert "master" in roles
        assert "guru" in roles

    def test_grouped_by_status(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                data = client.get("/api/queue").json()
        assert "sprint" in data["grouped"]
        assert "hold" in data["grouped"]
        assert "in_progress" in data["grouped"]
        assert "akien" in data["grouped"]

    def test_no_db_returns_empty(self):
        from starlette.testclient import TestClient
        app = _make_app()
        # /api/queue reads the store directly — an empty store yields an empty list.
        with patch("unseen_university.ticket_store.list", return_value=[]):
            with TestClient(app) as client:
                data = client.get("/api/queue").json()
        assert data["count"] == 0
        assert data["tickets"] == []

    def test_ticket_fields_present(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                data = client.get("/api/queue").json()
        t = data["tickets"][0]
        for field in ("id", "title", "status", "size", "worker", "gate", "role"):
            assert field in t


# ── /queue page ───────────────────────────────────────────────────────────────


class TestPageQueue:
    def test_returns_200(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                resp = client.get("/queue")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_renders_ticket_ids(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert "T-web-ui-queue-view" in html
        assert "T-in-flight" in html

    def test_renders_status_groups(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
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
        assert "Akien (needs your action)" in html  # canonical akien label (_STATUS_LABEL), not "(legacy)"
        assert "akien (legacy)" not in html.lower()  # specific guard: akien must not revert

    def test_no_db_shows_unavailable(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("unseen_university.devices.web_server.server._db_conn", return_value=None):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert "DB unavailable" in html or "no-db" in html

    def test_queue_link_in_nav(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert 'href="/queue"' in html

    def test_auto_refresh_script_present(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert "30000" in html or "reload" in html

    def test_role_column_in_table(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert "Role" in html
        assert "master" in html

    def test_my_tickets_filter_shows_guru_only(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                html = client.get("/queue?view=mine").text
        assert "T-akien-setup" in html
        assert "T-web-ui-queue-view" not in html

    def test_my_tickets_tab_links_present(self):
        from starlette.testclient import TestClient
        app = _make_app()
        with _serving(_TICKET_ROWS):
            with TestClient(app) as client:
                html = client.get("/queue").text
        assert "view=mine" in html
        assert "My Tickets" in html

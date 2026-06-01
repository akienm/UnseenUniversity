"""
test_memory_scope_tiers.py — Unit tests for 4-tier memory scope factory functions.

Tests cover env-var wiring and error paths only — no live DB connections.
PGDatabaseProxy.__init__ calls ThreadedConnectionPool eagerly, so every test
that would construct a live proxy monkeypatches psycopg2.pool to capture the
dsn without opening a real connection.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_pool_dsn():
    """Return a mock ThreadedConnectionPool that captures the dsn kwarg."""
    captured = {}

    class FakePool:
        def __init__(self, minconn, maxconn, dsn):
            captured["dsn"] = dsn

    return FakePool, captured


# ---------------------------------------------------------------------------
# make_global_proxy
# ---------------------------------------------------------------------------


def test_make_global_proxy_raises_without_url():
    saved = os.environ.pop("UU_GLOBAL_KB_DB_URL", None)
    try:
        from unseen_university.db_proxy import make_global_proxy

        with pytest.raises(RuntimeError, match="UU_GLOBAL_KB_DB_URL not set"):
            make_global_proxy()
    finally:
        if saved is not None:
            os.environ["UU_GLOBAL_KB_DB_URL"] = saved


def test_make_global_proxy_uses_correct_url():
    sentinel = "postgresql://global:x@localhost/uu-global-kb"
    FakePool, captured = _capture_pool_dsn()

    with patch("psycopg2.pool.ThreadedConnectionPool", FakePool):
        os.environ["UU_GLOBAL_KB_DB_URL"] = sentinel
        try:
            from unseen_university.db_proxy import make_global_proxy

            proxy = make_global_proxy()
            assert captured["dsn"] == sentinel
        finally:
            os.environ.pop("UU_GLOBAL_KB_DB_URL", None)


def test_make_global_proxy_default_search_path():
    sentinel = "postgresql://global:x@localhost/uu-global-kb"
    FakePool, _ = _capture_pool_dsn()

    with patch("psycopg2.pool.ThreadedConnectionPool", FakePool):
        os.environ["UU_GLOBAL_KB_DB_URL"] = sentinel
        saved_sp = os.environ.pop("UU_GLOBAL_KB_SEARCH_PATH", None)
        try:
            from unseen_university.db_proxy import make_global_proxy

            proxy = make_global_proxy()
            assert proxy._search_path == "global,public"
        finally:
            os.environ.pop("UU_GLOBAL_KB_DB_URL", None)
            if saved_sp is not None:
                os.environ["UU_GLOBAL_KB_SEARCH_PATH"] = saved_sp


# ---------------------------------------------------------------------------
# make_agent_proxy
# ---------------------------------------------------------------------------


def test_make_agent_proxy_raises_without_any_url():
    saved_device = os.environ.pop("DEVICE_ID", None)
    saved_agent = os.environ.pop("IGOR_AGENT_DB_URL", None)
    saved_home = os.environ.pop("IGOR_HOME_DB_URL", None)
    saved_db = os.environ.pop("IGOR_DB_URL", None)
    try:
        from unseen_university.db_proxy import make_agent_proxy

        with pytest.raises(RuntimeError, match="IGOR_AGENT_DB_URL not set"):
            make_agent_proxy()
    finally:
        if saved_device is not None:
            os.environ["DEVICE_ID"] = saved_device
        if saved_agent is not None:
            os.environ["IGOR_AGENT_DB_URL"] = saved_agent
        if saved_home is not None:
            os.environ["IGOR_HOME_DB_URL"] = saved_home
        if saved_db is not None:
            os.environ["IGOR_DB_URL"] = saved_db


def test_make_agent_proxy_uses_device_id_env_var():
    """DEVICE_ID=granny-weatherwax → GRANNY_WEATHERWAX_AGENT_DB_URL."""
    sentinel = "postgresql://agent:x@localhost/granny-agent"
    FakePool, captured = _capture_pool_dsn()

    saved_device = os.environ.get("DEVICE_ID")
    saved_home = os.environ.pop("IGOR_HOME_DB_URL", None)
    os.environ["DEVICE_ID"] = "granny-weatherwax"
    os.environ["GRANNY_WEATHERWAX_AGENT_DB_URL"] = sentinel
    try:
        with patch("psycopg2.pool.ThreadedConnectionPool", FakePool):
            from unseen_university.db_proxy import make_agent_proxy

            make_agent_proxy()
            assert captured["dsn"] == sentinel
    finally:
        os.environ.pop("GRANNY_WEATHERWAX_AGENT_DB_URL", None)
        if saved_device is not None:
            os.environ["DEVICE_ID"] = saved_device
        else:
            os.environ.pop("DEVICE_ID", None)
        if saved_home is not None:
            os.environ["IGOR_HOME_DB_URL"] = saved_home


def test_make_agent_proxy_device_id_dot_normalization():
    """DEVICE_ID=CC.0 → CC_0_AGENT_DB_URL."""
    sentinel = "postgresql://agent:x@localhost/cc-agent"
    FakePool, captured = _capture_pool_dsn()

    saved_device = os.environ.get("DEVICE_ID")
    saved_home = os.environ.pop("IGOR_HOME_DB_URL", None)
    os.environ["DEVICE_ID"] = "CC.0"
    os.environ["CC_0_AGENT_DB_URL"] = sentinel
    try:
        with patch("psycopg2.pool.ThreadedConnectionPool", FakePool):
            from unseen_university.db_proxy import make_agent_proxy

            make_agent_proxy()
            assert captured["dsn"] == sentinel
    finally:
        os.environ.pop("CC_0_AGENT_DB_URL", None)
        if saved_device is not None:
            os.environ["DEVICE_ID"] = saved_device
        else:
            os.environ.pop("DEVICE_ID", None)
        if saved_home is not None:
            os.environ["IGOR_HOME_DB_URL"] = saved_home


def test_make_agent_proxy_fallback_to_igor_home():
    """When DEVICE_ID unset, falls back to IGOR_HOME_DB_URL for backward compat."""
    sentinel = "postgresql://igor:x@localhost/igor-wild-0001"
    FakePool, captured = _capture_pool_dsn()

    saved_device = os.environ.pop("DEVICE_ID", None)
    saved_agent = os.environ.pop("IGOR_AGENT_DB_URL", None)
    os.environ["IGOR_HOME_DB_URL"] = sentinel
    try:
        with patch("psycopg2.pool.ThreadedConnectionPool", FakePool):
            from unseen_university.db_proxy import make_agent_proxy

            make_agent_proxy()
            assert captured["dsn"] == sentinel
    finally:
        os.environ.pop("IGOR_HOME_DB_URL", None)
        if saved_device is not None:
            os.environ["DEVICE_ID"] = saved_device
        if saved_agent is not None:
            os.environ["IGOR_AGENT_DB_URL"] = saved_agent


# ---------------------------------------------------------------------------
# make_client_proxy
# ---------------------------------------------------------------------------


def test_make_client_proxy_raises_without_url():
    saved = os.environ.pop("AKIEN_CLIENT_DB_URL", None)
    try:
        from unseen_university.db_proxy import make_client_proxy

        with pytest.raises(RuntimeError, match="AKIEN_CLIENT_DB_URL not set"):
            make_client_proxy("akien")
    finally:
        if saved is not None:
            os.environ["AKIEN_CLIENT_DB_URL"] = saved


def test_make_client_proxy_uses_client_id_env_var():
    sentinel = "postgresql://client:x@localhost/akien-private"
    FakePool, captured = _capture_pool_dsn()

    os.environ["AKIEN_CLIENT_DB_URL"] = sentinel
    try:
        with patch("psycopg2.pool.ThreadedConnectionPool", FakePool):
            from unseen_university.db_proxy import make_client_proxy

            make_client_proxy("akien")
            assert captured["dsn"] == sentinel
    finally:
        os.environ.pop("AKIEN_CLIENT_DB_URL", None)


def test_make_client_proxy_separate_per_client():
    """Two different client_ids map to different env vars — no shared fallback."""
    FakePool_akien, captured_akien = _capture_pool_dsn()
    FakePool_leah, captured_leah = _capture_pool_dsn()

    os.environ["AKIEN_CLIENT_DB_URL"] = "postgresql://x@localhost/akien-db"
    os.environ["LEAH_CLIENT_DB_URL"] = "postgresql://x@localhost/leah-db"
    try:
        with patch("psycopg2.pool.ThreadedConnectionPool", FakePool_akien):
            from unseen_university.db_proxy import make_client_proxy

            make_client_proxy("akien")
        with patch("psycopg2.pool.ThreadedConnectionPool", FakePool_leah):
            make_client_proxy("leah")

        assert captured_akien["dsn"] != captured_leah["dsn"]
        assert "akien" in captured_akien["dsn"]
        assert "leah" in captured_leah["dsn"]
    finally:
        os.environ.pop("AKIEN_CLIENT_DB_URL", None)
        os.environ.pop("LEAH_CLIENT_DB_URL", None)


def test_make_client_proxy_default_search_path():
    FakePool, _ = _capture_pool_dsn()

    os.environ["LEAH_CLIENT_DB_URL"] = "postgresql://x@localhost/leah-db"
    saved_sp = os.environ.pop("LEAH_CLIENT_SEARCH_PATH", None)
    try:
        with patch("psycopg2.pool.ThreadedConnectionPool", FakePool):
            from unseen_university.db_proxy import make_client_proxy

            proxy = make_client_proxy("leah")
            assert proxy._search_path == "client,public"
    finally:
        os.environ.pop("LEAH_CLIENT_DB_URL", None)
        if saved_sp is not None:
            os.environ["LEAH_CLIENT_SEARCH_PATH"] = saved_sp

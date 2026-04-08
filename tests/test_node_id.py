"""Tests for wild_igor/igor/memory/node_id.py (D256)."""

import json
import os
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from wild_igor.igor.memory.node_id import (
    build_suffix,
    new_node_id,
    node_exists,
    node_locate,
    parse_node_id,
    register_node,
    ts_from_datetime,
)

# ── ID format ─────────────────────────────────────────────────────────────────


class TestNodeIdFormat:
    def test_base_format_no_suffix(self):
        """Without env vars, ID is exactly 20 digits."""
        with patch.dict(os.environ, {}, clear=False):
            # Ensure no suffix env vars
            for k in (
                "IGOR_SWARM_NAME",
                "IGOR_MULTI_MACHINE",
                "IGOR_INSTANCE_ID",
                "IGOR_COE_NAME",
            ):
                os.environ.pop(k, None)
            nid = new_node_id(suffix="")
        assert len(nid) == 20
        assert nid.isdigit()

    def test_format_looks_like_timestamp(self):
        nid = new_node_id(suffix="")
        assert len(nid) == 20
        # Should parse as YYYYMMDDHHMMSSuuuuuu
        parsed = parse_node_id(nid)
        assert parsed
        assert parsed["datetime"].year >= 2026

    def test_explicit_suffix(self):
        nid = new_node_id(suffix="testswarm")
        assert nid.endswith(".testswarm")
        ts_part = nid.split(".")[0]
        assert len(ts_part) == 20

    def test_empty_suffix_suppresses(self):
        nid = new_node_id(suffix="")
        assert "." not in nid

    def test_swarm_name_env_var(self):
        with patch.dict(os.environ, {"IGOR_SWARM_NAME": "myswarm"}):
            nid = new_node_id()
        assert ".myswarm" in nid

    def test_instance_suffix_only_when_nondefault(self):
        with patch.dict(
            os.environ,
            {
                "IGOR_SWARM_NAME": "sw",
                "IGOR_INSTANCE_ID": "Igor-wild-0001",  # default — no suffix
            },
        ):
            nid = new_node_id()
        parts = nid.split(".")
        assert len(parts) == 2  # ts + swarm only

    def test_instance_suffix_when_nondefault(self):
        with patch.dict(
            os.environ,
            {
                "IGOR_SWARM_NAME": "sw",
                "IGOR_INSTANCE_ID": "igor_wild_0002",  # non-default
            },
        ):
            nid = new_node_id()
        parts = nid.split(".")
        assert len(parts) == 3  # ts + swarm + instance

    def test_coe_suffix_requires_swarm_and_instance(self):
        """COE suffix only appears when swarm AND non-default instance are set."""
        with patch.dict(
            os.environ,
            {
                "IGOR_SWARM_NAME": "sw",
                "IGOR_INSTANCE_ID": "igor_wild_0002",
                "IGOR_COE_NAME": "attention_0",
            },
        ):
            nid = new_node_id()
        parts = nid.split(".")
        assert parts[-1] == "attention_0"


# ── Uniqueness under burst ────────────────────────────────────────────────────


class TestUniqueness:
    def test_100_ids_are_unique(self):
        """100 IDs generated in a tight loop must all be unique."""
        ids = [new_node_id(suffix="") for _ in range(100)]
        assert len(set(ids)) == 100

    def test_100_ids_are_monotonic(self):
        """IDs (timestamp part) must be non-decreasing."""
        ids = [new_node_id(suffix="").split(".")[0] for _ in range(100)]
        assert ids == sorted(ids)

    def test_thread_safety_200_ids_unique(self):
        """200 IDs from 4 threads must all be unique."""
        results = []
        lock = threading.Lock()

        def gen():
            local = [new_node_id(suffix="") for _ in range(50)]
            with lock:
                results.extend(local)

        threads = [threading.Thread(target=gen) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 200
        assert len(set(results)) == 200


# ── parse_node_id ─────────────────────────────────────────────────────────────


class TestParseNodeId:
    def test_parse_plain(self):
        nid = "20260329143022123456"
        parsed = parse_node_id(nid)
        assert parsed["timestamp_str"] == "20260329143022123456"
        assert parsed["datetime"].year == 2026
        assert parsed["swarm"] is None

    def test_parse_with_swarm(self):
        nid = "20260329143022123456.akiendelllinux"
        parsed = parse_node_id(nid)
        assert parsed["swarm"] == "akiendelllinux"
        assert parsed["instance"] is None

    def test_parse_full(self):
        nid = "20260329143022123456.akiendelllinux.igor_wild_0002.coe_0"
        parsed = parse_node_id(nid)
        assert parsed["swarm"] == "akiendelllinux"
        assert parsed["instance"] == "igor_wild_0002"
        assert parsed["coe"] == "coe_0"

    def test_parse_invalid(self):
        assert parse_node_id("CP1") == {}
        assert parse_node_id("BL_abc12345") == {}
        assert parse_node_id("") == {}

    def test_ts_from_datetime(self):
        dt = datetime(2026, 3, 29, 14, 30, 22, 123456, tzinfo=timezone.utc)
        ts = ts_from_datetime(dt)
        assert ts == "20260329143022123456"


# ── Registry (Postgres + Redis) ───────────────────────────────────────────────


def _pg_available() -> bool:
    """Return True if Postgres is reachable AND node_registry table exists."""
    try:
        import psycopg2

        url = os.getenv(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        conn = psycopg2.connect(url, connect_timeout=2)
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='node_registry'"
        )
        has_table = cur.fetchone() is not None
        conn.close()
        return has_table
    except Exception:
        return False


@pytest.mark.skipif(not _pg_available(), reason="Postgres not reachable")
class TestRegistry:
    def test_register_and_locate_postgres(self):
        """register_node writes to Postgres; node_locate reads it back."""
        db_url = os.getenv(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        nid = new_node_id(suffix="test")
        register_node(nid, "memories", f"fake_{nid}", db_url=db_url)
        result = node_locate(nid, db_url=db_url)
        assert result is not None
        assert result["table_name"] == "memories"
        assert result["row_id"] == f"fake_{nid}"

    def test_node_exists_true(self):
        db_url = os.getenv(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        nid = new_node_id(suffix="test")
        register_node(nid, "memories", f"exists_{nid}", db_url=db_url)
        assert node_exists(nid, db_url=db_url)

    def test_node_exists_false(self):
        db_url = os.getenv(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        assert not node_exists("00000000000000000000.ghost", db_url=db_url)

    def test_redis_cache_hit(self):
        """node_locate returns cached value from Redis without hitting Postgres."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(
            {"table_name": "memories", "row_id": "cached_row"}
        )
        with patch("wild_igor.igor.memory.node_id._get_redis", return_value=mock_redis):
            result = node_locate("20260329000000000001.test")
        assert result == {"table_name": "memories", "row_id": "cached_row"}
        mock_redis.get.assert_called_once_with("node:20260329000000000001.test")

    def test_redis_miss_falls_back_to_postgres(self):
        """On Redis miss, node_locate falls through to Postgres."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        db_url = os.getenv(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        nid = new_node_id(suffix="fallback")
        register_node(nid, "reading_list", f"rl_{nid}", db_url=db_url)
        with patch("wild_igor.igor.memory.node_id._get_redis", return_value=mock_redis):
            result = node_locate(nid, db_url=db_url)
        assert result is not None
        assert result["table_name"] == "reading_list"

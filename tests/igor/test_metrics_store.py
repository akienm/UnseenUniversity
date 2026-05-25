"""
test_metrics_store.py — T-metrics-store

Tests for the time-series metrics accumulation store.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _ensure_table():
    """Create the table if the migration hasn't run yet."""
    db_url = os.environ.get("IGOR_HOME_DB_URL")
    if not db_url:
        pytest.skip("IGOR_HOME_DB_URL not set")
    import psycopg2

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SET search_path TO infra, clan, instance, public")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS infra.metrics ("
        " id SERIAL PRIMARY KEY,"
        " metric_name TEXT NOT NULL,"
        " metric_value DOUBLE PRECISION NOT NULL,"
        " tags JSONB DEFAULT '{}',"
        " recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
        " instance_id TEXT DEFAULT ''"
        ")"
    )
    conn.commit()
    conn.close()
    yield
    # Cleanup test entries
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SET search_path TO infra, clan, instance, public")
    cur.execute("DELETE FROM metrics WHERE metric_name LIKE 'TEST_%%'")
    conn.commit()
    conn.close()


class TestRecordMetric:
    def test_record_and_query(self):
        from lab.utility_closet.metrics_store import query_metrics, record_metric

        record_metric("TEST_basic", 42.0)
        results = query_metrics("TEST_basic", hours=1)
        assert len(results) >= 1
        assert results[0]["metric_value"] == 42.0

    def test_record_with_tags(self):
        from lab.utility_closet.metrics_store import query_metrics, record_metric

        record_metric("TEST_tagged", 99.0, tags={"session": "test"})
        results = query_metrics("TEST_tagged", hours=1)
        assert results[0]["tags"]["session"] == "test"

    def test_multiple_values(self):
        from lab.utility_closet.metrics_store import query_metrics, record_metric

        record_metric("TEST_multi", 1.0)
        record_metric("TEST_multi", 2.0)
        record_metric("TEST_multi", 3.0)
        results = query_metrics("TEST_multi", hours=1)
        assert len(results) == 3
        # Most recent first
        assert results[0]["metric_value"] == 3.0


class TestLatestMetric:
    def test_latest(self):
        from lab.utility_closet.metrics_store import latest_metric, record_metric

        record_metric("TEST_latest", 10.0)
        record_metric("TEST_latest", 20.0)
        val = latest_metric("TEST_latest")
        assert val == 20.0

    def test_latest_nonexistent(self):
        from lab.utility_closet.metrics_store import latest_metric

        val = latest_metric("TEST_nonexistent_xyz")
        assert val is None


class TestListMetricNames:
    def test_list_includes_recorded(self):
        from lab.utility_closet.metrics_store import list_metric_names, record_metric

        record_metric("TEST_listable", 1.0)
        names = list_metric_names()
        assert "TEST_listable" in names


class TestMigrationEntry:
    def test_m052_exists(self):
        from devices.igor.memory.cortex import _SCHEMA_MIGRATIONS

        names = {name for name, _ in _SCHEMA_MIGRATIONS}
        assert "m052_metrics_store" in names
        assert "m052_metrics_store_idx" in names

"""Tests for T-igor-boredom-background-goals: boredom→foreman_scan wiring."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ── Pure unit tests (no DB, no Igor imports) ─────────────────────────────────


def test_boredom_source_twm_push_includes_category():
    """BoredomSource must pass category='boredom_detected' so _check_twm_trigger_habits
    can find the entry via twm_read(category='boredom_detected')."""
    import inspect
    from unseen_university.devices.igor.cognition import push_sources as ps

    src = inspect.getsource(ps.BoredomSource.push)
    # The twm_push call in BoredomSource.push must include category=
    assert 'category="boredom_detected"' in src, (
        "BoredomSource.push() must pass category='boredom_detected' to twm_push "
        "so _check_twm_trigger_habits can query it correctly"
    )


def test_proc_boredom_foreman_seeded_in_db():
    """PROC_BOREDOM_FOREMAN habit must exist in the DB with correct wiring."""
    db_url = os.environ.get("UU_HOME_DB_URL", "")
    if not db_url:
        pytest.skip("UU_HOME_DB_URL not set")

    import psycopg2

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metadata FROM memories WHERE id = 'PROC_BOREDOM_FOREMAN'",
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None, "PROC_BOREDOM_FOREMAN habit not found in DB — run seed_boredom_foreman.py"
    meta = row[0] if isinstance(row[0], dict) else __import__("json").loads(row[0])
    assert meta.get("twm_trigger") == "BOREDOM_DETECTED", (
        f"Expected twm_trigger='BOREDOM_DETECTED', got {meta.get('twm_trigger')!r}"
    )
    assert meta.get("code_ref") == "tools.worker_foreman:foreman_scan", (
        f"Expected code_ref='tools.worker_foreman:foreman_scan', got {meta.get('code_ref')!r}"
    )


def test_foreman_scan_exists_and_is_callable():
    """foreman_scan() must be importable and callable."""
    from unseen_university.devices.igor.tools.worker_foreman import foreman_scan
    assert callable(foreman_scan)


def test_seed_script_is_idempotent():
    """seed_boredom_foreman.seed() must run twice without error (ON CONFLICT DO UPDATE)."""
    db_url = os.environ.get("UU_HOME_DB_URL", "")
    if not db_url:
        pytest.skip("UU_HOME_DB_URL not set")

    from unseen_university.devices.igor.tools.seed_boredom_foreman import seed
    seed()  # first run
    seed()  # idempotent second run

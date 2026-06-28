"""
test_persistent_relationships.py — T-pr-schema-seed.

Tests the foundation of the persistent-relationships epic:

  - Seed script is idempotent and creates the expected facia + trees rows
  - pr_list returns the seeded relationships
  - pr_get resolves by id, by display_name, and by lowercase short form
  - pr_touch updates last_activity_ts
  - pr_set_status enforces valid values and persists
  - pr_update_weight clamps to [0.0, 2.0] and persists

These tests run against the live Postgres because the schema is structural
DB state. The seed script is idempotent so re-running tests is safe; teardown
restores facia weights/status to their initial 'active'/1.0 baseline.
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _home_conn():
    """Return a psycopg2 connection with correct search_path for the test session."""
    import psycopg2

    db_url = os.environ.get(
        "UU_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    sp = os.environ.get("IGOR_HOME_SEARCH_PATH") or "clan,infra,public"
    cur.execute(f"SET search_path TO {sp}")
    cur.close()
    return conn


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    """Run the seed script once before all tests. Idempotent — safe to re-run."""
    from unseen_university.devices.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0
    yield
    # Restore baseline so other tests / sessions see active/1.0
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    _pr.pr_set_status(name="PR_AKIEN", status="active")
    _pr.pr_set_status(name="PR_IGORS_PROJECT", status="active")
    # Reset weight to 1.0 by computing the delta
    for facia_id in ("PR_AKIEN", "PR_IGORS_PROJECT"):
        row = _pr._resolve_facia(facia_id)
        if row:
            current = float(row["metadata"].get("cumulative_investment_weight", 1.0))
            delta = 1.0 - current
            if abs(delta) > 1e-9:
                _pr.pr_update_weight(name=facia_id, delta=delta)


# ── seed + DB state ──────────────────────────────────────────────────────────


def test_seed_creates_pr_root_facia():
    conn = _home_conn()
    cur = conn.cursor()
    cur.execute("SELECT memory_type, metadata::text FROM memories WHERE id = 'PR_ROOT'")
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "REFERENCE"
    assert "persistent_relationships_root" in row[1]


def test_seed_creates_pr_akien_and_igors_project_facia():
    import json

    conn = _home_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, metadata FROM memories WHERE id IN ('PR_AKIEN','PR_IGORS_PROJECT') ORDER BY id"
    )
    rows = cur.fetchall()
    conn.close()

    assert len(rows) == 2
    ids = {r[0] for r in rows}
    assert ids == {"PR_AKIEN", "PR_IGORS_PROJECT"}

    for _id, meta in rows:
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta.get("facia_role") == "persistent_relationship"
        assert meta.get("parent_facia_id") == "PR_ROOT"
        assert meta.get("status") == "active"
        assert meta.get("cumulative_investment_weight") == 1.0
        assert "last_activity_ts" in meta
        assert "display_name" in meta
        assert "relationship_type" in meta


def test_seed_creates_three_trees_rows():
    conn = _home_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT name, facia_id FROM trees "
        "WHERE name IN ('persistent_relationships','pr_akien','pr_igors_project') "
        "ORDER BY name"
    )
    rows = cur.fetchall()
    conn.close()

    by_name = {r[0]: r[1] for r in rows}
    assert by_name == {
        "persistent_relationships": "PR_ROOT",
        "pr_akien": "PR_AKIEN",
        "pr_igors_project": "PR_IGORS_PROJECT",
    }


def test_seed_is_idempotent():
    """Running the seed twice doesn't create duplicates."""
    from unseen_university.devices.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0

    conn = _home_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM memories WHERE id = 'PR_AKIEN'")
    count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM trees WHERE name = 'pr_akien'")
    tree_count = cur.fetchone()[0]
    conn.close()

    assert count == 1
    assert tree_count == 1


# ── CRUD tools ───────────────────────────────────────────────────────────────


def test_pr_list_returns_seeded_relationships():
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    out = _pr.pr_list()
    assert "PR_AKIEN" in out
    assert "PR_IGORS_PROJECT" in out
    assert "Akien" in out
    assert "The Igors Project" in out


def test_pr_get_resolves_by_id():
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    out = _pr.pr_get(name="PR_AKIEN")
    assert "id: PR_AKIEN" in out
    assert "display_name: Akien" in out
    assert "relationship_type: person" in out
    assert "status: active" in out


def test_pr_get_resolves_by_display_name():
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    out = _pr.pr_get(name="Akien")
    assert "id: PR_AKIEN" in out


def test_pr_get_resolves_by_lowercase_short_form():
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    out = _pr.pr_get(name="akien")
    assert "id: PR_AKIEN" in out


def test_pr_get_returns_not_found_for_nonexistent():
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    out = _pr.pr_get(name="NotARealRelationship")
    assert "No persistent-relationship" in out


def test_pr_touch_updates_last_activity_ts():
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    before = _pr._resolve_facia("PR_AKIEN")
    before_ts = before["metadata"]["last_activity_ts"]
    time.sleep(0.01)
    out = _pr.pr_touch(name="PR_AKIEN")
    assert "Touched PR_AKIEN" in out
    after = _pr._resolve_facia("PR_AKIEN")
    after_ts = after["metadata"]["last_activity_ts"]
    assert after_ts > before_ts


def test_pr_set_status_persists_and_rejects_invalid():
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    out = _pr.pr_set_status(name="PR_AKIEN", status="dormant")
    assert "status=dormant" in out

    row = _pr._resolve_facia("PR_AKIEN")
    assert row["metadata"]["status"] == "dormant"

    out_invalid = _pr.pr_set_status(name="PR_AKIEN", status="bogus")
    assert "Invalid status" in out_invalid

    # Restore for other tests
    _pr.pr_set_status(name="PR_AKIEN", status="active")


def test_pr_update_weight_clamps_to_range():
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    # Guard: reset to 1.0 baseline in case cross-test state pollution left a different value
    row = _pr._resolve_facia("PR_AKIEN")
    if row:
        current = float(row["metadata"].get("cumulative_investment_weight", 1.0))
        if abs(current - 1.0) > 1e-9:
            _pr.pr_update_weight(name="PR_AKIEN", delta=1.0 - current)

    # Start from 1.0, push above ceiling
    _pr.pr_update_weight(name="PR_AKIEN", delta=10.0)
    row = _pr._resolve_facia("PR_AKIEN")
    assert row["metadata"]["cumulative_investment_weight"] == 2.0

    # Push below floor
    _pr.pr_update_weight(name="PR_AKIEN", delta=-100.0)
    row = _pr._resolve_facia("PR_AKIEN")
    assert row["metadata"]["cumulative_investment_weight"] == 0.0

    # Restore to baseline
    _pr.pr_update_weight(name="PR_AKIEN", delta=1.0)
    row = _pr._resolve_facia("PR_AKIEN")
    assert row["metadata"]["cumulative_investment_weight"] == 1.0

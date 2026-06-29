"""
test_pr_consolidation.py — T-pr-consolidation.

Tests the offline integration pass that walks accretions and updates
the relationship facia. Each test seeds a known set of accretions,
runs consolidation, and asserts the resulting facia state +
consolidation_summary memory.

Cleanup deletes test accretions and resets PR_AKIEN weight to 1.0
between tests so each runs against a known baseline.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    from unseen_university.devices.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0


def _delete_test_accretions():
    import psycopg2

    db_url = os.environ.get(
        "UU_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM memories WHERE id LIKE 'PRA_%' "
        "AND metadata @> '{\"test_marker\": true}'::jsonb"
    )
    conn.close()


def _reset_akien_weight_to_one():
    """Restore PR_AKIEN cumulative_investment_weight to exactly 1.0."""
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    row = _pr._resolve_facia("PR_AKIEN")
    if row:
        current = float(row["metadata"].get("cumulative_investment_weight", 1.0))
        delta = 1.0 - current
        if abs(delta) > 1e-9:
            _pr.pr_update_weight(name="PR_AKIEN", delta=delta)


@pytest.fixture(autouse=True)
def cleanup_each():
    yield
    _delete_test_accretions()
    _reset_akien_weight_to_one()


def _seed_accretion(facia_id: str, content_type: str, ord_n: int = 0) -> str:
    """Helper: create a tagged test accretion."""
    from unseen_university.devices.igor.tools import pr_accretion as _pra

    return _pra.pr_accrete(
        facia_id=facia_id,
        content_type=content_type,
        narrative=f"test {content_type} {ord_n}",
        metadata={"test_marker": True, "ord": ord_n},
    )


# ── compute_weight_delta formula ─────────────────────────────────────────────


def test_weight_delta_inactive_pass_decays():
    from unseen_university.devices.igor.tools.pr_consolidation import compute_weight_delta

    delta = compute_weight_delta({"exchange": 0, "marker": 0, "commitment": 0})
    assert delta == -0.02


def test_weight_delta_single_exchange_small_positive():
    from unseen_university.devices.igor.tools.pr_consolidation import compute_weight_delta

    delta = compute_weight_delta({"exchange": 1, "marker": 0, "commitment": 0})
    # 1 * 1.0 weight * 0.01 = 0.01
    assert delta == pytest.approx(0.01, abs=1e-6)


def test_weight_delta_caps_at_max_per_pass():
    from unseen_university.devices.igor.tools.pr_consolidation import compute_weight_delta

    # 100 exchanges → would be 1.0 raw, but capped at 0.10
    delta = compute_weight_delta({"exchange": 100, "marker": 0, "commitment": 0})
    assert delta == 0.10


def test_weight_delta_commitments_weighted_higher_than_exchanges():
    from unseen_university.devices.igor.tools.pr_consolidation import compute_weight_delta

    # 1 commitment (weight 5) vs 1 exchange (weight 1)
    just_exchange = compute_weight_delta({"exchange": 1})
    just_commitment = compute_weight_delta({"commitment": 1})
    assert just_commitment > just_exchange
    # 1 commitment = 5 * 0.01 = 0.05
    assert just_commitment == pytest.approx(0.05, abs=1e-6)


def test_weight_delta_markers_between_exchange_and_commitment():
    from unseen_university.devices.igor.tools.pr_consolidation import compute_weight_delta

    e = compute_weight_delta({"exchange": 1})
    m = compute_weight_delta({"marker": 1})
    c = compute_weight_delta({"commitment": 1})
    assert e < m < c


# ── pr_consolidate ───────────────────────────────────────────────────────────


def test_consolidate_with_no_accretions_decays_weight():
    from unseen_university.devices.igor.tools import pr_consolidation as _prc
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    # Use a since_ts in the future so no accretions match — simulates inactivity
    summary = _prc.pr_consolidate(facia_id="PR_AKIEN", since_ts="2099-01-01")
    assert "Consolidation pass: 0 accretions reviewed" in summary

    row = _pr._resolve_facia("PR_AKIEN")
    new_weight = float(row["metadata"]["cumulative_investment_weight"])
    # 1.0 - 0.02 = 0.98
    assert new_weight == pytest.approx(0.98, abs=1e-6)


def test_consolidate_with_active_accretions_increases_weight():
    from unseen_university.devices.igor.tools import pr_consolidation as _prc
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    _seed_accretion("PR_AKIEN", "exchange", 1)
    _seed_accretion("PR_AKIEN", "exchange", 2)
    _seed_accretion("PR_AKIEN", "marker", 3)
    _seed_accretion("PR_AKIEN", "commitment", 4)

    summary = _prc.pr_consolidate(facia_id="PR_AKIEN")
    # We seeded at least 2 exchange + 1 marker + 1 commitment. Other tests
    # (or main.py's _process_inner integration during the test session) may
    # have left additional accretions on PR_AKIEN, so we assert minimum
    # counts rather than exact ones. The weighted activity from our seeds
    # alone (1*2 + 3 + 5 = 10) hits the cap, so the weight delta is +0.10
    # regardless of how many extras are present — that's the cap working.
    assert "exchange=" in summary
    assert "marker=" in summary
    assert "commitment=" in summary
    assert "+0.100" in summary

    row = _pr._resolve_facia("PR_AKIEN")
    new_weight = float(row["metadata"]["cumulative_investment_weight"])
    assert new_weight == pytest.approx(1.10, abs=1e-6)


def test_consolidate_writes_consolidation_summary_memory():
    from unseen_university.devices.igor.tools import pr_consolidation as _prc
    from unseen_university.devices.igor.tools import pr_accretion as _pra

    _seed_accretion("PR_AKIEN", "exchange", 1)
    _prc.pr_consolidate(facia_id="PR_AKIEN")

    rows = _pra.pr_recent_accretions("PR_AKIEN", limit=10)
    summaries = [
        r for r in rows if r["metadata"].get("content_type") == "consolidation_summary"
    ]
    assert len(summaries) >= 1
    summary = summaries[0]
    assert "consolidation_counts" in summary["metadata"]
    assert "weight_before" in summary["metadata"]
    assert "weight_after" in summary["metadata"]
    assert summary["metadata"]["accretions_reviewed"] >= 1

    # Cleanup the consolidation_summary itself (it isn't tagged
    # test_marker since pr_accrete doesn't carry that flag through).
    import psycopg2

    db_url = os.environ.get(
        "UU_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM memories WHERE id = %s",
        (summary["id"],),
    )
    conn.close()


def test_consolidate_clamps_weight_to_max():
    from unseen_university.devices.igor.tools import pr_consolidation as _prc
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    # Push baseline up to near-max
    _pr.pr_update_weight(name="PR_AKIEN", delta=0.95)  # 1.0 + 0.95 = 1.95
    row = _pr._resolve_facia("PR_AKIEN")
    assert row["metadata"]["cumulative_investment_weight"] == pytest.approx(1.95)

    # Seed enough activity to overflow
    for n in range(5):
        _seed_accretion("PR_AKIEN", "commitment", n)

    _prc.pr_consolidate(facia_id="PR_AKIEN")

    row = _pr._resolve_facia("PR_AKIEN")
    new_weight = float(row["metadata"]["cumulative_investment_weight"])
    # Capped at 2.0
    assert new_weight == 2.0


def test_consolidate_clamps_weight_to_min():
    from unseen_university.devices.igor.tools import pr_consolidation as _prc
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    # Push baseline down to near-zero
    _pr.pr_update_weight(name="PR_AKIEN", delta=-0.99)  # 1.0 - 0.99 = 0.01
    row = _pr._resolve_facia("PR_AKIEN")
    assert row["metadata"]["cumulative_investment_weight"] == pytest.approx(0.01)

    # Run several inactive passes
    _prc.pr_consolidate(facia_id="PR_AKIEN", since_ts="2099-01-01")
    _prc.pr_consolidate(facia_id="PR_AKIEN", since_ts="2099-01-01")
    _prc.pr_consolidate(facia_id="PR_AKIEN", since_ts="2099-01-01")

    row = _pr._resolve_facia("PR_AKIEN")
    new_weight = float(row["metadata"]["cumulative_investment_weight"])
    # Floored at 0.0
    assert new_weight == 0.0


def test_consolidate_unknown_facia_returns_error_string():
    from unseen_university.devices.igor.tools import pr_consolidation as _prc

    out = _prc.pr_consolidate(facia_id="PR_NONEXISTENT")
    assert "[ERROR]" in out
    assert "PR_NONEXISTENT" in out


# ── pr_consolidate_all ───────────────────────────────────────────────────────


def test_consolidate_all_processes_active_facia():
    from unseen_university.devices.igor.tools import pr_consolidation as _prc

    out = _prc.pr_consolidate_all(since_ts="2099-01-01")
    assert "PR_AKIEN" in out
    assert "PR_IGORS_PROJECT" in out


def test_consolidate_all_skips_dormant():
    from unseen_university.devices.igor.tools import pr_consolidation as _prc
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    # Set PR_IGORS_PROJECT dormant temporarily
    _pr.pr_set_status(name="PR_IGORS_PROJECT", status="dormant")
    try:
        out = _prc.pr_consolidate_all(since_ts="2099-01-01")
        assert "PR_AKIEN" in out
        assert "PR_IGORS_PROJECT" not in out
    finally:
        _pr.pr_set_status(name="PR_IGORS_PROJECT", status="active")

"""Tests for wild_igor/igor/cognition/focus_state.py (T-igor-focus-state)."""

import pytest


@pytest.fixture(autouse=True)
def _clean_focus(pg_test_schema):
    if pg_test_schema is None:
        pytest.skip("pg_test_schema not available")
    from wild_igor.igor.cognition import focus_state

    focus_state.reset_focus()
    yield
    focus_state.reset_focus()


@pytest.fixture
def fs(pg_test_schema):
    from wild_igor.igor.cognition import focus_state

    return focus_state


def test_first_update_inserts_candidate(fs):
    fs.update_from_activation("MEM_001", 0.8)
    row = fs.get_focus()
    assert row is not None
    assert row["memory_id"] == "MEM_001"
    assert abs(row["activation_score"] - 0.8) < 0.001
    assert row["status"] == "candidate"


def test_below_hysteresis_does_not_displace(fs):
    """Score 1.19× current does not displace (below 1.2 HYSTERESIS_FACTOR)."""
    fs.update_from_activation("MEM_A", 1.0)
    fs.update_from_activation("MEM_B", 1.19)  # 1.19 < 1.0 * 1.2
    row = fs.get_focus()
    assert row["memory_id"] == "MEM_A"


def test_at_hysteresis_displaces(fs):
    """Score exactly 1.2× current displaces the focus."""
    fs.update_from_activation("MEM_A", 1.0)
    fs.update_from_activation("MEM_B", 1.2)  # exactly 1.0 * 1.2
    row = fs.get_focus()
    assert row["memory_id"] == "MEM_B"


def test_advance_cycle_expires_committed(fs):
    """advance_cycle() returns True when committed focus reaches expires_at_cycle."""
    import psycopg2, os

    fs.update_from_activation("MEM_X", 0.9)
    # Manually commit focus with expires_at_cycle = current + EXPIRY_CYCLES
    conn = psycopg2.connect(
        os.environ.get(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
    )
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE instance.focus_state "
                "SET status='committed', committed_at=now(), "
                "    expires_at_cycle=ne_cycle_counter + %s "
                "WHERE id=1",
                (fs.EXPIRY_CYCLES,),
            )
    conn.close()

    # Advance EXPIRY_CYCLES times — last call should return True
    expired = False
    for _ in range(fs.EXPIRY_CYCLES):
        expired = fs.advance_cycle()
    assert expired is True

    # After expiry: status reverts to candidate
    row = fs.get_focus()
    assert row["status"] == "candidate"
    assert row["expires_at_cycle"] is None


def test_focus_history_capped_at_5(fs):
    """focus_history ring buffer caps at HISTORY_CAP (5) entries."""
    for i in range(7):
        fs.update_from_activation(f"MEM_{i}", float(i + 1) * 2.0)  # always displaces

    row = fs.get_focus()
    history = row["focus_history"]
    assert isinstance(history, list)
    assert len(history) <= fs.HISTORY_CAP


def test_reset_focus_clears_row(fs):
    fs.update_from_activation("MEM_Z", 0.5)
    assert fs.get_focus() is not None
    fs.reset_focus()
    assert fs.get_focus() is None

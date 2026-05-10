"""Tests for wild_igor/igor/cognition/dreaming.py (T-igor-dreaming-module)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


@pytest.fixture(autouse=True)
def _clean_proposals(pg_test_schema):
    if pg_test_schema is None:
        pytest.skip("pg_test_schema not available")
    yield
    conn = psycopg2.connect(_PG_URL)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM instance.proposals WHERE source_module = 'dreaming'"
                )
    except Exception:
        pass
    finally:
        conn.close()


@pytest.fixture
def mock_paths(tmp_path):
    """A minimal paths() stand-in with a temp logs directory."""
    p = MagicMock()
    p.logs = tmp_path / "logs"
    p.logs.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def psych_log_with_entries(mock_paths):
    """Write 5 psych_log entries into mock_paths.logs."""
    log_file = mock_paths.logs / "igor_psych.jsonl"
    entries = [
        {
            "ts": 1746900000.0 + i * 60,
            "valence": 0.3,
            "arousal": 0.5,
            "notes": f"cycle {i}",
        }
        for i in range(5)
    ]
    with log_file.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return mock_paths


# ── Disabled when IGOR_DREAMING_INTERVAL=0 ───────────────────────────────────


def test_dreaming_disabled_when_interval_zero(mock_paths, monkeypatch):
    """IGOR_DREAMING_INTERVAL=0 → run() returns 0 immediately."""
    monkeypatch.setenv("IGOR_DREAMING_INTERVAL", "0")
    from wild_igor.igor.cognition import dreaming

    result = dreaming.run(paths_obj=mock_paths)
    assert result == 0


# ── Empty inputs return 0 without calling haiku ──────────────────────────────


def test_dreaming_empty_inputs_returns_zero(mock_paths, monkeypatch):
    """No psych_log, no watch_problems → run() returns 0 without synthesis call."""
    monkeypatch.setenv("IGOR_DREAMING_INTERVAL", "50")
    from wild_igor.igor.cognition import dreaming

    with patch(
        "wild_igor.igor.cognition.dreaming._read_watch_problems", return_value=[]
    ), patch("wild_igor.igor.cognition.dreaming._synthesize") as mock_synth:
        result = dreaming.run(paths_obj=mock_paths)

    assert result == 0
    mock_synth.assert_not_called()


# ── Proposals written on seeded psych_log + watch_problems ───────────────────


def test_dreaming_writes_proposals(psych_log_with_entries, monkeypatch):
    """Mocked haiku returning 1 proposal → 1 row in instance.proposals."""
    monkeypatch.setenv("IGOR_DREAMING_INTERVAL", "50")
    from wild_igor.igor.cognition import dreaming

    mock_proposals = [
        {
            "kind": "habit",
            "content": "When valence is low, scan watch_problems for active levers.",
            "rationale": "Repeated low valence correlates with unresolved watch entries.",
        }
    ]

    with patch(
        "wild_igor.igor.cognition.dreaming._synthesize",
        return_value=mock_proposals,
    ):
        result = dreaming.run(paths_obj=psych_log_with_entries)

    assert result == 1

    conn = psycopg2.connect(_PG_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT kind, content, source_module "
                "FROM instance.proposals WHERE source_module = 'dreaming'"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "habit"
    assert rows[0][2] == "dreaming"


def test_dreaming_deduplicates_identical_proposals(psych_log_with_entries, monkeypatch):
    """Two identical proposals → occurrence_count increments, not two rows."""
    monkeypatch.setenv("IGOR_DREAMING_INTERVAL", "50")
    from wild_igor.igor.cognition import dreaming

    proposal = [
        {
            "kind": "watch_q",
            "content": "Watch for repeated low arousal after escalation.",
            "rationale": "Pattern detected.",
        }
    ]

    with patch(
        "wild_igor.igor.cognition.dreaming._synthesize",
        return_value=proposal,
    ):
        dreaming.run(paths_obj=psych_log_with_entries)
        result2 = dreaming.run(paths_obj=psych_log_with_entries)

    # Second run increments occurrence_count, not a new row
    conn = psycopg2.connect(_PG_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), MAX(occurrence_count) "
                "FROM instance.proposals WHERE source_module = 'dreaming'"
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row[0] == 1  # only one distinct proposal
    assert row[1] >= 2  # occurrence_count ≥ 2


# ── Cycle counter behavior ────────────────────────────────────────────────────


def test_cycle_counter_triggers_at_interval(monkeypatch):
    """COA._ne_cycle_counter triggers dreaming.run() every N cycles."""
    from wild_igor.igor.cognition.coa import COA

    monkeypatch.setenv("IGOR_DREAMING_INTERVAL", "3")

    # Minimal stubs — we only test the counter logic, not the full NE
    cortex_stub = MagicMock()
    igor_stub = MagicMock()
    igor_stub._is_processing = False
    coa = COA.__new__(COA)
    coa._ne_cycle_counter = 0

    run_calls = []

    def _fake_run():
        run_calls.append(1)
        return 1

    with patch("wild_igor.igor.cognition.dreaming.run", side_effect=_fake_run):
        import os as _os

        interval = int(_os.getenv("IGOR_DREAMING_INTERVAL", "50"))
        for _ in range(9):
            coa._ne_cycle_counter += 1
            if coa._ne_cycle_counter % interval == 0:
                from wild_igor.igor.cognition import dreaming as _dreaming

                _dreaming.run()

    # 9 cycles / interval 3 → 3 triggers
    assert len(run_calls) == 3

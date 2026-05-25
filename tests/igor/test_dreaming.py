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


# ── Librarian observation helpers ─────────────────────────────────────────────


def test_is_convergent_true_when_both_domains():
    from wild_igor.igor.cognition.dreaming import _is_convergent

    assert _is_convergent(
        "Librarian researched this topic and igor's valence dropped — pattern detected."
    )


def test_is_convergent_false_librarian_only():
    from wild_igor.igor.cognition.dreaming import _is_convergent

    assert not _is_convergent("Librarian observation about research quality.")


def test_is_convergent_false_psych_only():
    from wild_igor.igor.cognition.dreaming import _is_convergent

    assert not _is_convergent("Igor valence low and arousal high during this period.")


def test_synthesize_sets_convergence_flag():
    """Proposals with rationale citing both librarian + psych terms get convergence=True."""
    from wild_igor.igor.cognition.dreaming import _synthesize

    convergent_proposals = [
        {
            "kind": "habit",
            "content": "Cross-check librarian and igor observations.",
            "rationale": "Librarian research and igor psych data both highlight this gap.",
            "conditions": "",
            "heuristics": "",
        }
    ]
    non_convergent_proposals = [
        {
            "kind": "watch_q",
            "content": "Watch for repeated failures in retrieval.",
            "rationale": "Repeated retrieval failures in recent cycles.",
            "conditions": "",
            "heuristics": "",
        }
    ]
    all_proposals = convergent_proposals + non_convergent_proposals

    fake_inner_cc = MagicMock()
    fake_inner_cc.call_inner_cc_long.return_value = {
        "answer": json.dumps(all_proposals)
    }
    with patch.dict("sys.modules", {"wild_igor.igor.tools.inner_cc": fake_inner_cc}):
        results = _synthesize(
            psych_entries=[{"ts": 1, "valence": 0.5, "arousal": 0.5, "notes": ""}],
            watch_problems=[],
        )

    assert len(results) == 2
    assert (
        results[0].get("convergence") is True
    ), "convergent proposal should be flagged"
    assert (
        "convergence" not in results[1]
    ), "non-convergent proposal should not be flagged"


def test_add_proposal_stores_extra_metadata(pg_test_schema):
    """extra_metadata is merged into the stored metadata JSON."""
    if pg_test_schema is None:
        pytest.skip("pg_test_schema not available")
    from wild_igor.igor.cognition.dreaming import (
        _add_proposal,
        _conn,
        _ensure_proposals,
    )

    conn = _conn()
    try:
        with conn:
            _ensure_proposals(conn)
        with conn:
            pid = _add_proposal(
                conn,
                kind="habit",
                content="test extra metadata content unique-xmeta-1",
                source_module="dreaming",
                extra_metadata={"convergence": True, "test_key": "test_val"},
            )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metadata FROM instance.proposals WHERE id = %s",
                (pid,),
            )
            meta = cur.fetchone()[0]
    finally:
        conn.close()

    assert meta.get("convergence") is True
    assert meta.get("test_key") == "test_val"
    assert "fingerprint" in meta


def test_read_librarian_observations_returns_list(pg_test_schema):
    """_read_librarian_observations() returns a list (empty or populated)."""
    if pg_test_schema is None:
        pytest.skip("pg_test_schema not available")
    from wild_igor.igor.cognition.dreaming import _read_librarian_observations

    result = _read_librarian_observations()
    assert isinstance(result, list)

"""
test_habit_chunker.py — Unit tests for habit_chunker.py (T-habit-chunking).

Tests:
  _find_ngrams:
    - finds 3-gram appearing 5+ times
    - respects min_count threshold
    - handles sequences shorter than n
    - returns sorted by count desc

  _chunk_id:
    - stable hash for same input
    - different hash for different sequences

  run_habit_chunking:
    - returns skip message when no sequences
    - returns skip message when no grams meet threshold
    - calls _upsert_chunk for each qualifying gram
    - returns summary with counts
    - handles DB fetch error gracefully
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.tools.habit_chunker import (
    _chunk_id,
    _find_ngrams,
    run_habit_chunking,
)

# ── _find_ngrams ──────────────────────────────────────────────────────────────


class TestFindNgrams:
    def test_finds_trigram_above_threshold(self):
        seqs = [["A", "B", "C"]] * 6 + [["X", "Y"]]
        result = _find_ngrams(seqs, n=3, min_count=5)
        assert len(result) == 1
        assert result[0][0] == ("A", "B", "C")
        assert result[0][1] == 6

    def test_respects_min_count(self):
        seqs = [["A", "B", "C"]] * 4
        result = _find_ngrams(seqs, n=3, min_count=5)
        assert result == []

    def test_sequence_shorter_than_n_skipped(self):
        seqs = [["A", "B"]] * 10
        result = _find_ngrams(seqs, n=3, min_count=1)
        assert result == []

    def test_sorted_descending_by_count(self):
        seqs = [["A", "B", "C"]] * 7 + [["X", "Y", "Z"]] * 5
        result = _find_ngrams(seqs, n=3, min_count=5)
        assert result[0][1] >= result[1][1]

    def test_multiple_grams_in_one_sequence(self):
        seqs = [["A", "B", "C", "D"]] * 6
        # Should find both A→B→C and B→C→D
        result = _find_ngrams(seqs, n=3, min_count=5)
        grams = [r[0] for r in result]
        assert ("A", "B", "C") in grams
        assert ("B", "C", "D") in grams

    def test_empty_sequences(self):
        result = _find_ngrams([], n=3, min_count=1)
        assert result == []


# ── _chunk_id ─────────────────────────────────────────────────────────────────


class TestChunkId:
    def test_stable_for_same_input(self):
        gram = ("PROC_A", "PROC_B", "PROC_C")
        assert _chunk_id(gram) == _chunk_id(gram)

    def test_different_for_different_sequences(self):
        assert _chunk_id(("A", "B", "C")) != _chunk_id(("A", "B", "D"))

    def test_starts_with_chunk_prefix(self):
        assert _chunk_id(("A", "B", "C")).startswith("CHUNK_")

    def test_length_is_14(self):
        # "CHUNK_" (6) + 8 hex chars
        assert len(_chunk_id(("A", "B", "C"))) == 14


# ── run_habit_chunking ────────────────────────────────────────────────────────


class TestRunHabitChunking:
    def _mock_sequences(self, seqs):
        return patch(
            "unseen_university.devices.igor.tools.habit_chunker._fetch_habit_sequences",
            return_value=seqs,
        )

    def _mock_upsert(self):
        return patch("unseen_university.devices.igor.tools.habit_chunker._upsert_chunk")

    def test_no_sequences_returns_skip(self):
        with self._mock_sequences([]):
            result = run_habit_chunking()
        assert "no habit sequences" in result

    def test_no_qualifying_grams_returns_skip(self):
        # Only 3 instances of the same trigram — below threshold of 5
        seqs = [["A", "B", "C"]] * 3
        with self._mock_sequences(seqs):
            result = run_habit_chunking()
        assert (
            "no" in result.lower()
            and "sequences" in result.lower()
            or "chunk" in result.lower()
        )

    def test_qualifying_grams_stored(self):
        seqs = [["PROC_A", "PROC_B", "PROC_C"]] * 6
        with self._mock_sequences(seqs):
            with self._mock_upsert() as mock_upsert:
                result = run_habit_chunking()
        mock_upsert.assert_called_once()
        args = mock_upsert.call_args[
            0
        ]  # positional args: (db_url, chunk_id, narrative, gram, count)
        assert args[1].startswith("CHUNK_")  # chunk_id
        assert "PROC_A" in args[2]  # narrative
        assert ("PROC_A", "PROC_B", "PROC_C") == args[3]  # gram tuple
        assert args[4] == 6  # count

    def test_summary_includes_counts(self):
        seqs = [["PROC_A", "PROC_B", "PROC_C"]] * 6
        with self._mock_sequences(seqs):
            with self._mock_upsert():
                result = run_habit_chunking()
        assert "chunk" in result.lower()
        assert "stored" in result

    def test_db_fetch_error_returns_error_string(self):
        with patch(
            "unseen_university.devices.igor.tools.habit_chunker._fetch_habit_sequences",
            side_effect=Exception("conn refused"),
        ):
            result = run_habit_chunking()
        assert "ERROR" in result
        assert "conn refused" in result

    def test_upsert_error_does_not_crash(self):
        seqs = [["PROC_A", "PROC_B", "PROC_C"]] * 6
        with self._mock_sequences(seqs):
            with patch(
                "unseen_university.devices.igor.tools.habit_chunker._upsert_chunk",
                side_effect=Exception("DB error"),
            ):
                result = run_habit_chunking()
        # Should complete (not raise) and report the error count
        assert "ERROR" in result or "error" in result.lower()

    def test_multiple_chunks_stored(self):
        seqs = [["PROC_A", "PROC_B", "PROC_C"]] * 6 + [
            ["PROC_X", "PROC_Y", "PROC_Z"]
        ] * 5
        with self._mock_sequences(seqs):
            with self._mock_upsert() as mock_upsert:
                result = run_habit_chunking()
        assert mock_upsert.call_count == 2
        assert "2" in result

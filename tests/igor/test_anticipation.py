"""
test_anticipation.py — Tests for anticipation pull (T-anticipation-pull).

Covers: record_closure, predict_valence, weighted_ticket_score,
        history ring eviction, recency weighting, tag overlap logic.
Uses a temp file to avoid touching the live closure_history.json.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))

import igor.cognition.anticipation as ant


def _patch_path(tmp: Path):
    """Context manager that redirects anticipation history to a temp file."""
    return patch.object(ant, "_history_path", return_value=tmp)


class TestRecordAndLoad(unittest.TestCase):
    def test_record_creates_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        tmp.unlink()  # start absent
        try:
            with _patch_path(tmp):
                ant.record_closure("T-foo", ["a", "b"], 0.7)
            data = json.loads(tmp.read_text())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["ticket_id"], "T-foo")
            self.assertAlmostEqual(data[0]["valence"], 0.7)
            self.assertEqual(data[0]["tags"], ["a", "b"])
        finally:
            tmp.unlink(missing_ok=True)

    def test_record_appends(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            with _patch_path(tmp):
                ant.record_closure("T-1", ["x"], 0.5)
                ant.record_closure("T-2", ["y"], -0.3)
                data = json.loads(tmp.read_text())
            self.assertEqual(len(data), 2)
        finally:
            tmp.unlink(missing_ok=True)

    def test_ring_eviction(self):
        """History capped at HISTORY_MAX; oldest entries evicted."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            with patch.object(ant, "_HISTORY_MAX", 3), _patch_path(tmp):
                for i in range(5):
                    ant.record_closure(f"T-{i}", ["tag"], float(i) * 0.1)
                data = json.loads(tmp.read_text())
            self.assertEqual(len(data), 3)
            # Newest 3 survive
            self.assertEqual(data[0]["ticket_id"], "T-2")
            self.assertEqual(data[-1]["ticket_id"], "T-4")
        finally:
            tmp.unlink(missing_ok=True)

    def test_corrupt_file_returns_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json{{")
            tmp = Path(f.name)
        try:
            with _patch_path(tmp):
                result = ant._load()
            self.assertEqual(result, [])
        finally:
            tmp.unlink(missing_ok=True)

    def test_missing_file_returns_empty(self):
        tmp = Path(tempfile.mktemp(suffix=".json"))
        with _patch_path(tmp):
            result = ant._load()
        self.assertEqual(result, [])


class TestPredictValence(unittest.TestCase):
    def _make_history(self, entries: list[dict], tmp: Path) -> None:
        tmp.write_text(json.dumps(entries))

    def test_no_history_returns_zero(self):
        tmp = Path(tempfile.mktemp(suffix=".json"))
        with _patch_path(tmp):
            v = ant.predict_valence(["routing"])
        self.assertEqual(v, 0.0)

    def test_empty_tags_returns_zero(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            self._make_history(
                [
                    {
                        "ticket_id": "T-x",
                        "tags": ["a"],
                        "valence": 0.8,
                        "ts": time.time(),
                    }
                ],
                tmp,
            )
            with _patch_path(tmp):
                v = ant.predict_valence([])
            self.assertEqual(v, 0.0)
        finally:
            tmp.unlink(missing_ok=True)

    def test_matching_tags_contributes(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            self._make_history(
                [
                    {
                        "ticket_id": "T-1",
                        "tags": ["routing"],
                        "valence": 0.6,
                        "ts": time.time(),
                    },
                    {
                        "ticket_id": "T-2",
                        "tags": ["routing"],
                        "valence": 0.8,
                        "ts": time.time(),
                    },
                ],
                tmp,
            )
            with _patch_path(tmp):
                v = ant.predict_valence(["routing"])
            # Should be positive (both entries positive)
            self.assertGreater(v, 0.0)
            self.assertLessEqual(v, 1.0)
        finally:
            tmp.unlink(missing_ok=True)

    def test_no_matching_tags_returns_zero(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            self._make_history(
                [
                    {
                        "ticket_id": "T-1",
                        "tags": ["routing"],
                        "valence": 0.9,
                        "ts": time.time(),
                    }
                ],
                tmp,
            )
            with _patch_path(tmp):
                v = ant.predict_valence(["tests", "docs"])
            self.assertEqual(v, 0.0)
        finally:
            tmp.unlink(missing_ok=True)

    def test_recency_weighting_favors_recent(self):
        """More recent entries with higher valence → prediction > simple average."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            # Entry 0 (older, lower valence) then entry 1 (newer, higher valence)
            self._make_history(
                [
                    {"ticket_id": "T-old", "tags": ["tag"], "valence": 0.1, "ts": 0.0},
                    {
                        "ticket_id": "T-new",
                        "tags": ["tag"],
                        "valence": 0.9,
                        "ts": time.time(),
                    },
                ],
                tmp,
            )
            with _patch_path(tmp):
                v = ant.predict_valence(["tag"])
            # Simple average = 0.5; recency-weighted should be > 0.5
            self.assertGreater(v, 0.5)
        finally:
            tmp.unlink(missing_ok=True)

    def test_partial_tag_overlap_matches(self):
        """Only one tag needs to match for a closure to contribute."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            self._make_history(
                [
                    {
                        "ticket_id": "T-1",
                        "tags": ["routing", "tests"],
                        "valence": 0.7,
                        "ts": time.time(),
                    }
                ],
                tmp,
            )
            with _patch_path(tmp):
                v = ant.predict_valence(["tests", "docs"])
            self.assertGreater(v, 0.0)
        finally:
            tmp.unlink(missing_ok=True)


class TestWeightedTicketScore(unittest.TestCase):
    def test_no_history_equals_priority(self):
        tmp = Path(tempfile.mktemp(suffix=".json"))
        with _patch_path(tmp):
            score = ant.weighted_ticket_score(3, ["routing"])
        self.assertEqual(score, 3.0)

    def test_positive_valence_lowers_score(self):
        """Anticipated positive completion → lower sort score → picked sooner."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            tmp.write_text(
                json.dumps(
                    [
                        {
                            "ticket_id": "T-1",
                            "tags": ["routing"],
                            "valence": 1.0,
                            "ts": time.time(),
                        }
                    ]
                )
            )
            with _patch_path(tmp):
                score = ant.weighted_ticket_score(2, ["routing"])
            # priority=2, weight=0.3, predicted≈1.0 → score ≈ 1.7 < 2.0
            self.assertLess(score, 2.0)
        finally:
            tmp.unlink(missing_ok=True)

    def test_negative_valence_raises_score(self):
        """Anticipated negative completion → higher sort score → deprioritized."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            tmp.write_text(
                json.dumps(
                    [
                        {
                            "ticket_id": "T-bad",
                            "tags": ["chore"],
                            "valence": -0.8,
                            "ts": time.time(),
                        }
                    ]
                )
            )
            with _patch_path(tmp):
                score = ant.weighted_ticket_score(1, ["chore"])
            # priority=1, predicted≈-0.8 → score ≈ 1.24 > 1.0
            self.assertGreater(score, 1.0)
        finally:
            tmp.unlink(missing_ok=True)

    def test_disabled_returns_raw_priority(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            tmp.write_text(
                json.dumps(
                    [
                        {
                            "ticket_id": "T-1",
                            "tags": ["routing"],
                            "valence": 0.9,
                            "ts": time.time(),
                        }
                    ]
                )
            )
            with _patch_path(tmp), patch.dict(
                os.environ, {"IGOR_ANTICIPATION_ENABLED": "false"}
            ):
                score = ant.weighted_ticket_score(5, ["routing"])
            self.assertEqual(score, 5.0)
        finally:
            tmp.unlink(missing_ok=True)


class TestHistorySummary(unittest.TestCase):
    def test_empty(self):
        tmp = Path(tempfile.mktemp(suffix=".json"))
        with _patch_path(tmp):
            s = ant.history_summary()
        self.assertIn("empty", s)

    def test_nonempty(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        try:
            tmp.write_text(
                json.dumps(
                    [
                        {
                            "ticket_id": "T-done",
                            "tags": ["x"],
                            "valence": 0.6,
                            "ts": time.time(),
                        }
                    ]
                )
            )
            with _patch_path(tmp):
                s = ant.history_summary()
            self.assertIn("T-done", s)
            self.assertIn("+0.60", s)
        finally:
            tmp.unlink(missing_ok=True)


class _MockCortex:
    """Minimal cortex stub for record_completion tests."""

    def __init__(self, ring_entries=None):
        self._ring = ring_entries or []
        self.written = []

    def read_ring_memory(self, limit=5, category=None):
        return self._ring[:limit]

    def write_ring(self, content, category=None):
        self.written.append({"content": content, "category": category})


class TestRecordCompletion(unittest.TestCase):
    def test_none_cortex_is_noop(self):
        """No cortex → silent no-op, no exception."""
        ant.record_completion("hello", "hi there", None)  # should not raise

    def test_ordinary_turn_writes_ack(self):
        """Low NE surprise (delta < 0.4) → COMPLETION_ACK ring entry."""
        cortex = _MockCortex(
            ring_entries=[{"content": "NE_SURPRISE|predicted=X|actual=Y|delta=0.25"}]
        )
        ant.record_completion("ordinary input", "ordinary reply", cortex)
        self.assertEqual(len(cortex.written), 1)
        self.assertIn("COMPLETION_ACK", cortex.written[0]["content"])
        self.assertIn("ordinary", cortex.written[0]["content"])
        self.assertEqual(cortex.written[0]["category"], "completion_trace")

    def test_noteworthy_turn_writes_noteworthy(self):
        """High NE surprise (delta >= 0.4) → COMPLETION_NOTEWORTHY ring entry."""
        cortex = _MockCortex(
            ring_entries=[{"content": "NE_SURPRISE|predicted=X|actual=None|delta=0.80"}]
        )
        ant.record_completion("surprise input", "unexpected reply", cortex)
        self.assertEqual(len(cortex.written), 1)
        self.assertIn("COMPLETION_NOTEWORTHY", cortex.written[0]["content"])
        self.assertEqual(cortex.written[0]["category"], "completion_trace")

    def test_no_ne_surprise_in_ring_defaults_ordinary(self):
        """Ring has no NE_SURPRISE entries → delta=0.0 → COMPLETION_ACK."""
        cortex = _MockCortex(ring_entries=[{"content": "LATENCY|total_ms=120"}])
        ant.record_completion("hello", "reply", cortex)
        self.assertEqual(len(cortex.written), 1)
        self.assertIn("COMPLETION_ACK", cortex.written[0]["content"])

    def test_at_boundary_040_is_noteworthy(self):
        """Exactly 0.4 is noteworthy (>= threshold)."""
        cortex = _MockCortex(
            ring_entries=[{"content": "NE_SURPRISE|predicted=A|actual=B|delta=0.40"}]
        )
        ant.record_completion("border input", "border reply", cortex)
        self.assertIn("COMPLETION_NOTEWORTHY", cortex.written[0]["content"])

    def test_pipe_in_input_is_sanitised(self):
        """Pipe characters in input are replaced to avoid CSB parsing issues."""
        cortex = _MockCortex()
        ant.record_completion("input|with|pipes", "reply", cortex)
        written = cortex.written[0]["content"]
        # snippet should replace | with /
        self.assertNotIn("input|with", written)

    def test_cortex_exception_does_not_raise(self):
        """If cortex.read_ring_memory raises, record_completion swallows it."""

        class BadCortex:
            def read_ring_memory(self, **_):
                raise RuntimeError("db offline")

            def write_ring(self, *_, **__):
                pass

        ant.record_completion("hello", "reply", BadCortex())  # must not raise


if __name__ == "__main__":
    unittest.main()

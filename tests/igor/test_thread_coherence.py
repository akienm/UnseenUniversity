"""
Tests for T-thread-coherence: ThreadCoherenceSource.

Covers:
- weighted_jaccard: identical sets → 1.0
- weighted_jaccard: no overlap → 0.0
- weighted_jaccard: partial overlap → correct ratio
- weighted_jaccard: empty inputs → 0.0
- weighted_jaccard: weighted asymmetry
- _parse_turn_traces: returns empty list for missing file
- _parse_turn_traces: parses valid turn trace blocks
- _extract_nodes: extracts {id: score} from bg_scoring.top
- _extract_nodes: returns empty dict on missing/malformed data
- push: skips when same turn_id repeated
- push: skips when thread_id differs between turns
- push: pushes normal TWM entry when coherent
- push: pushes drift TWM entry when score below threshold
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_source():
    """Instantiate ThreadCoherenceSource with all external deps mocked."""
    import types

    # Stub out heavy imports before loading push_sources
    for mod_name in [
        "unseen_university.devices.igor.igor_base",
        "unseen_university.devices.igor.paths",
        "unseen_university.devices.igor.cognition.forensic_logger",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    # Minimal IgorBase
    igor_base_mod = sys.modules["unseen_university.devices.igor.igor_base"]
    if not hasattr(igor_base_mod, "IgorBase"):
        igor_base_mod.IgorBase = object

    # Minimal paths()
    paths_mod = sys.modules["unseen_university.devices.igor.paths"]
    if not hasattr(paths_mod, "paths"):
        _p = MagicMock()
        _p.return_value = MagicMock()
        paths_mod.paths = _p

    # Minimal forensic_logger
    fl_mod = sys.modules["unseen_university.devices.igor.cognition.forensic_logger"]
    if not hasattr(fl_mod, "log_error"):
        fl_mod.log_error = lambda **kw: None

    from unseen_university.devices.igor.cognition.push_sources import ThreadCoherenceSource

    return ThreadCoherenceSource()


# ── weighted_jaccard ───────────────────────────────────────────────────────────


class TestWeightedJaccard(unittest.TestCase):
    def setUp(self):
        self.src = _make_source()

    def test_identical_sets(self):
        a = {"A": 1.0, "B": 0.8}
        self.assertAlmostEqual(self.src.weighted_jaccard(a, a), 1.0)

    def test_no_overlap(self):
        a = {"A": 1.0}
        b = {"B": 1.0}
        self.assertAlmostEqual(self.src.weighted_jaccard(a, b), 0.0)

    def test_partial_overlap(self):
        # A shared, B only in a, C only in b — all weight 1.0
        # num = min(1,1) = 1.0; den = max(1,0) + max(1,0) + max(0,1) = 3.0
        a = {"A": 1.0, "B": 1.0}
        b = {"A": 1.0, "C": 1.0}
        self.assertAlmostEqual(self.src.weighted_jaccard(a, b), 1 / 3)

    def test_empty_a(self):
        self.assertEqual(self.src.weighted_jaccard({}, {"A": 1.0}), 0.0)

    def test_empty_b(self):
        self.assertEqual(self.src.weighted_jaccard({"A": 1.0}, {}), 0.0)

    def test_both_empty(self):
        self.assertEqual(self.src.weighted_jaccard({}, {}), 0.0)

    def test_weighted_asymmetry(self):
        # Shared key A: min(2.0, 1.0)=1.0; max(2.0, 1.0)=2.0
        a = {"A": 2.0}
        b = {"A": 1.0}
        self.assertAlmostEqual(self.src.weighted_jaccard(a, b), 0.5)

    def test_full_overlap_different_weights(self):
        # Both sets have same keys; min/max reduces to sum(min)/sum(max)
        a = {"X": 2.0, "Y": 1.0}
        b = {"X": 1.0, "Y": 2.0}
        # num = 1+1=2; den = 2+2=4
        self.assertAlmostEqual(self.src.weighted_jaccard(a, b), 0.5)


# ── _parse_turn_traces ─────────────────────────────────────────────────────────


class TestParseTurnTraces(unittest.TestCase):
    def setUp(self):
        self.src = _make_source()

    def test_missing_file_returns_empty(self):
        result = self.src._parse_turn_traces(Path("/nonexistent/path.log"))
        self.assertEqual(result, [])

    def test_parses_two_blocks(self):
        trace1 = {
            "turn_id": "aaa",
            "thread_id": "web:shared",
            "bg_scoring": {"top": []},
        }
        trace2 = {
            "turn_id": "bbb",
            "thread_id": "web:shared",
            "bg_scoring": {"top": []},
        }
        content = (
            f"=== turn aaa | web:shared | ts ===\n{json.dumps(trace1)}\n=== END ===\n"
            f"=== turn bbb | web:shared | ts ===\n{json.dumps(trace2)}\n=== END ==="
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = Path(f.name)
        try:
            result = self.src._parse_turn_traces(tmp)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["turn_id"], "aaa")
            self.assertEqual(result[1]["turn_id"], "bbb")
        finally:
            tmp.unlink(missing_ok=True)

    def test_skips_malformed_blocks(self):
        good = {"turn_id": "ok", "bg_scoring": {"top": []}}
        content = (
            "=== turn bad ===\nnot json at all\n=== END ===\n"
            f"=== turn ok ===\n{json.dumps(good)}\n=== END ==="
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = Path(f.name)
        try:
            result = self.src._parse_turn_traces(tmp)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["turn_id"], "ok")
        finally:
            tmp.unlink(missing_ok=True)


# ── _extract_nodes ─────────────────────────────────────────────────────────────


class TestExtractNodes(unittest.TestCase):
    def setUp(self):
        self.src = _make_source()

    def test_extracts_nodes(self):
        trace = {
            "bg_scoring": {
                "top": [
                    {"id": "PROC_HABIT_A", "score": 1.2, "type": ""},
                    {"id": "WINNOW_ABC123", "score": 1.1, "type": ""},
                ]
            }
        }
        result = self.src._extract_nodes(trace)
        self.assertEqual(result, {"PROC_HABIT_A": 1.2, "WINNOW_ABC123": 1.1})

    def test_empty_top(self):
        trace = {"bg_scoring": {"top": []}}
        self.assertEqual(self.src._extract_nodes(trace), {})

    def test_missing_bg_scoring(self):
        self.assertEqual(self.src._extract_nodes({}), {})

    def test_missing_id_skipped(self):
        trace = {"bg_scoring": {"top": [{"score": 1.0}]}}
        self.assertEqual(self.src._extract_nodes(trace), {})


# ── push() integration ─────────────────────────────────────────────────────────


def _make_trace(turn_id, thread_id, top_nodes):
    """Build a minimal turn trace dict."""
    return {
        "turn_id": turn_id,
        "thread_id": thread_id,
        "bg_scoring": {
            "top": [{"id": nid, "score": score, "type": ""} for nid, score in top_nodes]
        },
    }


def _write_traces(tmp_path, traces):
    """Write trace list to a temp log file named with today's date. Returns Path."""
    today = datetime.now().strftime("%Y%m%d")
    lines = []
    for t in traces:
        lines.append(f"=== turn {t['turn_id']} | {t['thread_id']} ===")
        lines.append(json.dumps(t))
        lines.append("=== END ===")
    p = tmp_path / f"turn_trace.{today}.log"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


class TestPush(unittest.TestCase):
    def setUp(self):
        self.src = _make_source()
        self.src._last_run = None
        self.src._last_turn_id = None
        self._tmpdir = tempfile.mkdtemp()
        self._tmp = Path(self._tmpdir)

    def _mock_cortex(self):
        c = MagicMock()
        c.twm_push.return_value = 42
        return c

    def _patch_paths(self, log_dir):
        """Patch paths().logs to point to log_dir."""
        mock_paths = MagicMock()
        mock_paths.logs = log_dir
        return patch(
            "unseen_university.devices.igor.cognition.push_sources.paths",
            return_value=mock_paths,
        )

    def test_skips_if_fewer_than_two_traces(self):
        log_dir = self._tmp / "logs1"
        log_dir.mkdir()
        _write_traces(log_dir, [_make_trace("t1", "web:shared", [("PROC_A", 1.0)])])
        cortex = self._mock_cortex()
        with self._patch_paths(log_dir):
            result = self.src.push(cortex)
        self.assertEqual(result, [])
        cortex.twm_push.assert_not_called()

    def test_skips_repeated_turn_id(self):
        log_dir = self._tmp / "logs2"
        log_dir.mkdir()
        traces = [
            _make_trace("t1", "web:shared", [("PROC_A", 1.0)]),
            _make_trace("t2", "web:shared", [("PROC_B", 1.0)]),
        ]
        _write_traces(log_dir, traces)
        cortex = self._mock_cortex()
        with self._patch_paths(log_dir):
            self.src.push(cortex)  # first call — processes t2
            self.src._last_run = None  # reset interval
            result = self.src.push(cortex)  # second call — same turn_id, skip
        self.assertEqual(cortex.twm_push.call_count, 1)
        self.assertEqual(result, [])

    def test_skips_cross_thread(self):
        log_dir = self._tmp / "logs3"
        log_dir.mkdir()
        traces = [
            _make_trace("t1", "web:shared", [("PROC_A", 1.0)]),
            _make_trace("t2", "stdin:main", [("PROC_B", 1.0)]),
        ]
        _write_traces(log_dir, traces)
        cortex = self._mock_cortex()
        with self._patch_paths(log_dir):
            result = self.src.push(cortex)
        self.assertEqual(result, [])
        cortex.twm_push.assert_not_called()

    def test_pushes_coherent_signal(self):
        log_dir = self._tmp / "logs4"
        log_dir.mkdir()
        # High overlap: same 3 nodes
        traces = [
            _make_trace(
                "t1", "web:shared", [("PROC_A", 1.2), ("PROC_B", 1.1), ("PROC_C", 1.0)]
            ),
            _make_trace(
                "t2", "web:shared", [("PROC_A", 1.2), ("PROC_B", 1.1), ("PROC_C", 1.0)]
            ),
        ]
        _write_traces(log_dir, traces)
        cortex = self._mock_cortex()
        with self._patch_paths(log_dir):
            result = self.src.push(cortex)
        self.assertEqual(result, [42])
        call_kwargs = cortex.twm_push.call_args[1]
        self.assertIn("THREAD_COHERENCE", call_kwargs["content_csb"])
        self.assertIn("drift=no", call_kwargs["content_csb"])
        self.assertFalse(call_kwargs["metadata"]["drift"])

    def test_pushes_drift_signal_on_low_overlap(self):
        log_dir = self._tmp / "logs5"
        log_dir.mkdir()
        # Zero overlap between turns
        traces = [
            _make_trace("t1", "web:shared", [("PROC_A", 1.0), ("PROC_B", 1.0)]),
            _make_trace("t2", "web:shared", [("PROC_X", 1.0), ("PROC_Y", 1.0)]),
        ]
        _write_traces(log_dir, traces)
        cortex = self._mock_cortex()
        with self._patch_paths(log_dir):
            result = self.src.push(cortex)
        self.assertEqual(result, [42])
        call_kwargs = cortex.twm_push.call_args[1]
        self.assertIn("drift=yes", call_kwargs["content_csb"])
        self.assertTrue(call_kwargs["metadata"]["drift"])
        # Drift should have higher salience
        self.assertGreater(call_kwargs["salience"], 0.4)


if __name__ == "__main__":
    unittest.main()

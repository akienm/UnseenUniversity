"""Tests for devices/evaluator/loop.py and devices/evaluator/uu_enrollment.py.

Maps directly to the three completion criteria:
  1. Deliberately bad output (fails R-uu-core) → filed ticket within one cycle.
  2. UU self-enrollment fires at least one improvement ticket on first run.
  3. Closed improvement ticket updates rubric score baseline.

All tests use a fake EvaluatorDevice so they are deterministic and inference-free.
cc_queue subprocess calls are patched to a capture function.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ── Fake evaluator ────────────────────────────────────────────────────────────


def _make_eval_result(
    verdict: str = "fail",
    score: float = 0.2,
    rubric_id: str = "R-uu-core",
    agent_id: str = "test-device",
    failing: list[str] | None = None,
) -> dict:
    failing = failing or ["no_sqlite"]
    judges = [
        {
            "judge_index": i,
            "passed": verdict == "pass",
            "score": score,
            "criteria_results": [
                {"name": c, "passed": verdict == "pass", "reasoning": "test"}
                for c in (failing if verdict == "fail" else ["ok"])
            ],
            "raw_response": "{}",
        }
        for i in range(3)
    ]
    return {
        "eval_id": "E-test0001",
        "agent_id": agent_id,
        "rubric_id": rubric_id,
        "score": score,
        "verdict": verdict,
        "judge_reasoning": judges,
        "evaluated_at": "2026-05-31T00:00:00+00:00",
    }


class FakeEvaluator:
    """Minimal fake: evaluate() returns a preset result; rubric_* use in-memory dict."""

    def __init__(self, preset: dict | None = None) -> None:
        self._preset = preset or _make_eval_result()
        self._rubrics: dict[str, dict] = {}
        self._history: list[dict] = []

    def evaluate(self, output: str, rubric_id: str, agent_id: str) -> dict:
        result = dict(self._preset)
        result["rubric_id"] = rubric_id
        result["agent_id"] = agent_id
        self._history.append(result)
        return result

    def rubric_create(self, name: str, criteria: list[dict]) -> str:
        import re

        rid = "R-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        self._rubrics[rid] = {"rubric_id": rid, "name": name, "criteria": criteria}
        return rid

    def rubric_list(self) -> list[dict]:
        return list(self._rubrics.values())

    def eval_history(self, agent_id: str, limit: int = 20) -> list[dict]:
        return [h for h in self._history if h["agent_id"] == agent_id][:limit]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_loop(evaluator, db_url: str = "", cc_queue_path: str = "/dev/null"):
    from unseen_university.devices.evaluator.loop import ObserveLearnImproveLoop

    return ObserveLearnImproveLoop(
        evaluator=evaluator,
        db_url=db_url,
        cc_queue_path=cc_queue_path,
    )


def _captured_add_calls(calls: list) -> list[dict]:
    """Extract ticket dicts from mocked subprocess.run calls to cc_queue add."""
    result = []
    for c in calls:
        args = c.args[0] if c.args else c[0][0]
        # args is [python, cc_queue, "add", json_str]
        if len(args) >= 4 and args[2] == "add":
            try:
                result.append(json.loads(args[3]))
            except (json.JSONDecodeError, IndexError):
                pass
    return result


# ── Criterion 1: bad output → filed ticket within one cycle ──────────────────


class TestCriterion1_BadOutputFilesTicket:
    def test_fail_verdict_files_ticket(self):
        """A single eval failure (score < threshold) produces a filed ticket."""
        evaluator = FakeEvaluator(preset=_make_eval_result(verdict="fail", score=0.2))
        loop = _make_loop(evaluator)

        mock_result = MagicMock(returncode=0, stdout="  added: T-learn-abc — title\n")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            cycle = loop.run_cycle(
                output="import sqlite3\nclass Bad: pass",
                rubric_id="R-uu-core",
                agent_id="bad-device",
                improve_threshold=0.6,
            )

        assert cycle["ticket_id"] is not None
        assert cycle["ticket_id"].startswith("T-learn-")
        assert cycle["eval_result"]["verdict"] == "fail"

        filed = _captured_add_calls(mock_run.call_args_list)
        assert len(filed) == 1
        t = filed[0]
        assert t["tags"] == ["Platform"]
        assert t["worker"] == "claude"
        assert "Affected files:" in t["description"]
        assert "Scope boundary:" in t["description"]
        assert "Completion criteria:" in t["description"]

    def test_pass_verdict_no_ticket(self):
        """A passing eval does not file a ticket."""
        evaluator = FakeEvaluator(preset=_make_eval_result(verdict="pass", score=0.9))
        loop = _make_loop(evaluator)

        with patch("subprocess.run") as mock_run:
            cycle = loop.run_cycle("good code", "R-uu-core", "good-device", 0.6)

        assert cycle["ticket_id"] is None
        mock_run.assert_not_called()

    def test_score_at_threshold_no_ticket(self):
        """Score exactly at threshold does not file a ticket."""
        evaluator = FakeEvaluator(preset=_make_eval_result(verdict="fail", score=0.6))
        loop = _make_loop(evaluator)

        with patch("subprocess.run") as mock_run:
            cycle = loop.run_cycle("code", "R-uu-core", "device", improve_threshold=0.6)

        assert cycle["ticket_id"] is None
        mock_run.assert_not_called()

    def test_ticket_id_is_deterministic(self):
        """Same agent+rubric always produces the same ticket id (dedup key)."""
        evaluator = FakeEvaluator(preset=_make_eval_result(verdict="fail", score=0.1))
        loop = _make_loop(evaluator)

        mock_result = MagicMock(returncode=0, stdout="  added: T-learn-abc — t\n")
        with patch("subprocess.run", return_value=mock_result):
            c1 = loop.run_cycle("bad", "R-uu-core", "mydevice", 0.6)
        with patch("subprocess.run", return_value=mock_result):
            c2 = loop.run_cycle("also bad", "R-uu-core", "mydevice", 0.6)

        assert c1["ticket_id"] == c2["ticket_id"]

    def test_dedup_skip_counts_as_success(self):
        """cc_queue 'skip (exists)' still returns the ticket_id (idempotent)."""
        evaluator = FakeEvaluator(preset=_make_eval_result(verdict="fail", score=0.1))
        loop = _make_loop(evaluator)

        skip_result = MagicMock(
            returncode=0, stdout="  skip (exists): T-learn-abc\nAdded 0 task(s).\n"
        )
        with patch("subprocess.run", return_value=skip_result):
            cycle = loop.run_cycle("bad", "R-uu-core", "mydevice", 0.6)

        assert cycle["ticket_id"] is not None

    def test_learn_writes_memory_on_fail(self):
        """Failed eval triggers a memory write call."""
        evaluator = FakeEvaluator(preset=_make_eval_result(verdict="fail", score=0.2))
        loop = _make_loop(evaluator)

        mock_run = MagicMock(returncode=0, stdout="  added: T-learn-abc — t\n")
        with patch("subprocess.run", return_value=mock_run):
            with patch("unseen_university.devices.librarian.memory_writer.write_memory") as mock_write:
                mock_write.return_value = {"id": "mem-001", "tags": [], "stored_at": ""}
                cycle = loop.run_cycle("bad code", "R-uu-core", "device-x", 0.6)

        mock_write.assert_called_once()
        assert cycle["memory_id"] == "mem-001"


# ── Criterion 2: UU self-enrollment fires at least one improvement ticket ─────


class TestCriterion2_UUEnrollment:
    def _make_bad_device_dir(
        self, tmp_path: Path, device_name: str = "bad-device"
    ) -> Path:
        """Create a minimal device.py that fails multiple R-uu-core criteria."""
        device_dir = tmp_path / device_name
        device_dir.mkdir()
        # Fails: uses sqlite3, no BaseDevice inheritance
        (device_dir / "device.py").write_text(
            "import sqlite3\n\nclass BadDevice:\n    def run(self): pass\n"
        )
        return device_dir

    def test_bad_device_gets_improvement_ticket(self, tmp_path):
        """A device that fails R-uu-core gets an improvement ticket filed."""
        from unseen_university.devices.evaluator.uu_enrollment import run_uu_enrollment

        device_dir = self._make_bad_device_dir(tmp_path, "bad-device")
        evaluator = FakeEvaluator(preset=_make_eval_result(verdict="fail", score=0.1))
        loop = _make_loop(evaluator)

        mock_run = MagicMock(returncode=0, stdout="  added: T-learn-x — t\n")
        with patch("subprocess.run", return_value=mock_run):
            with patch("unseen_university.devices.evaluator.uu_enrollment._DEVICES_ROOT", tmp_path):
                summary = run_uu_enrollment(
                    loop, rubric_id="R-uu-core", improve_threshold=0.6
                )

        assert summary["devices_scanned"] >= 1
        assert len(summary["tickets_filed"]) >= 1

    def test_good_device_no_ticket(self, tmp_path):
        """A device that passes R-uu-core does not get a ticket."""
        from unseen_university.devices.evaluator.uu_enrollment import run_uu_enrollment

        device_dir = tmp_path / "good-device"
        device_dir.mkdir()
        (device_dir / "device.py").write_text(
            "from unseen_university.device import BaseDevice\n\n"
            "class GoodDevice(BaseDevice):\n    pass\n"
        )
        # Also create a test file so has_smoke_test passes
        tests = tmp_path.parent / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "test_good_device.py").write_text("def test_placeholder(): pass\n")

        evaluator = FakeEvaluator(preset=_make_eval_result(verdict="pass", score=0.95))
        loop = _make_loop(evaluator)

        with patch("subprocess.run") as mock_run:
            with patch("unseen_university.devices.evaluator.uu_enrollment._DEVICES_ROOT", tmp_path):
                summary = run_uu_enrollment(
                    loop, rubric_id="R-uu-core", improve_threshold=0.6
                )

        assert len(summary["tickets_filed"]) == 0
        mock_run.assert_not_called()

    def test_build_output_includes_no_test_flag(self, tmp_path):
        """_build_output sets no_test=true when no test file exists."""
        from unseen_university.devices.evaluator.uu_enrollment import _build_output, _test_exists

        device_dir = tmp_path / "no-test-device"
        device_dir.mkdir()
        (device_dir / "device.py").write_text("class X: pass")

        output = _build_output(device_dir / "device.py")
        assert "no_test=true" in output

    def test_build_output_no_test_false_when_test_exists(self, tmp_path):
        """_build_output sets no_test=false when test file found."""
        from unseen_university.devices.evaluator.uu_enrollment import _build_output

        device_dir = tmp_path / "tested-device"
        device_dir.mkdir()
        (device_dir / "device.py").write_text("class X: pass")

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_tested_device.py").write_text("def test_x(): pass")

        with patch("unseen_university.devices.evaluator.uu_enrollment._UU_ROOT", tmp_path):
            output = _build_output(device_dir / "device.py")
        assert "no_test=false" in output

    def test_seed_uu_core_creates_rubric(self):
        """seed_uu_core_rubric() stores R-uu-core with expected criteria."""
        from unseen_university.devices.evaluator.uu_enrollment import seed_uu_core_rubric

        evaluator = FakeEvaluator()
        rubric_id = seed_uu_core_rubric(evaluator)

        assert rubric_id == "R-uu-core"
        rubrics = evaluator.rubric_list()
        assert any(r["rubric_id"] == "R-uu-core" for r in rubrics)
        criteria_names = [
            c["name"]
            for r in rubrics
            if r["rubric_id"] == "R-uu-core"
            for c in r["criteria"]
        ]
        assert "no_sqlite" in criteria_names
        assert "no_bare_except" in criteria_names
        assert "inherits_base_device" in criteria_names
        assert "has_smoke_test" in criteria_names


# ── Criterion 3: closed ticket updates rubric score baseline ─────────────────


class TestCriterion3_BaselineUpdate:
    def test_on_improvement_closed_updates_baseline(self):
        """After ticket close, rubric stores score_baseline from recent passes."""
        evaluator = FakeEvaluator()
        loop = _make_loop(evaluator)

        # Seed rubric
        evaluator.rubric_create(
            "uu-core", [{"name": "no_sqlite", "instruction": "..."}]
        )

        # Record some passing eval history
        pass_result = _make_eval_result(
            verdict="pass", score=0.85, rubric_id="R-uu-core", agent_id="my-device"
        )
        evaluator._history = [pass_result, pass_result]

        result = loop.on_improvement_closed(
            ticket_id="T-learn-abc1",
            agent_id="my-device",
            rubric_id="R-uu-core",
        )

        assert result["status"] == "updated"
        assert result["baseline"] == pytest.approx(0.85, abs=1e-4)
        assert result["rubric_id"] == "R-uu-core"

        # Baseline is persisted in the rubric
        baseline = loop.get_rubric_baseline("R-uu-core")
        assert baseline == pytest.approx(0.85, abs=1e-4)

    def test_baseline_averages_recent_passes(self):
        """Baseline is the mean of recent passing scores."""
        evaluator = FakeEvaluator()
        loop = _make_loop(evaluator)

        evaluator.rubric_create("uu-core", [{"name": "c", "instruction": "..."}])
        evaluator._history = [
            _make_eval_result(
                verdict="pass", score=0.8, rubric_id="R-uu-core", agent_id="d"
            ),
            _make_eval_result(
                verdict="pass", score=0.9, rubric_id="R-uu-core", agent_id="d"
            ),
            _make_eval_result(
                verdict="fail", score=0.3, rubric_id="R-uu-core", agent_id="d"
            ),
        ]

        result = loop.on_improvement_closed("T-x", "d", "R-uu-core")

        assert result["status"] == "updated"
        # Only the two passes are averaged: (0.8 + 0.9) / 2 = 0.85
        assert result["baseline"] == pytest.approx(0.85, abs=1e-4)

    def test_no_passes_returns_no_recent_passes(self):
        """If only fail history exists, baseline is not updated."""
        evaluator = FakeEvaluator()
        loop = _make_loop(evaluator)

        evaluator.rubric_create("uu-core", [{"name": "c", "instruction": "..."}])
        evaluator._history = [
            _make_eval_result(
                verdict="fail", score=0.1, rubric_id="R-uu-core", agent_id="d"
            ),
        ]

        result = loop.on_improvement_closed("T-x", "d", "R-uu-core")
        assert result["status"] == "no_recent_passes"
        assert result["baseline"] is None

    def test_baseline_sentinel_not_exposed_as_criterion(self):
        """The __baseline__ sentinel criterion is preserved as internal metadata."""
        evaluator = FakeEvaluator()
        loop = _make_loop(evaluator)

        evaluator.rubric_create(
            "uu-core",
            [{"name": "no_sqlite", "instruction": "check for sqlite"}],
        )
        evaluator._history = [
            _make_eval_result(
                verdict="pass", score=0.9, rubric_id="R-uu-core", agent_id="d"
            ),
        ]

        loop.on_improvement_closed("T-x", "d", "R-uu-core")
        rubric = next(
            r for r in evaluator.rubric_list() if r["rubric_id"] == "R-uu-core"
        )

        # Real criteria intact
        real = [c for c in rubric["criteria"] if c["name"] != "__baseline__"]
        assert any(c["name"] == "no_sqlite" for c in real)

        # Sentinel present with correct baseline
        sentinel = next(c for c in rubric["criteria"] if c["name"] == "__baseline__")
        assert sentinel["score_baseline"] == pytest.approx(0.9, abs=1e-4)

    def test_baseline_raised_on_second_close(self):
        """Calling on_improvement_closed twice raises the baseline each time."""
        evaluator = FakeEvaluator()
        loop = _make_loop(evaluator)

        evaluator.rubric_create("uu-core", [{"name": "c", "instruction": "..."}])

        evaluator._history = [
            _make_eval_result(
                verdict="pass", score=0.7, rubric_id="R-uu-core", agent_id="d"
            ),
        ]
        loop.on_improvement_closed("T-x1", "d", "R-uu-core")
        assert loop.get_rubric_baseline("R-uu-core") == pytest.approx(0.7, abs=1e-4)

        evaluator._history = [
            _make_eval_result(
                verdict="pass", score=0.9, rubric_id="R-uu-core", agent_id="d"
            ),
        ]
        loop.on_improvement_closed("T-x2", "d", "R-uu-core")
        assert loop.get_rubric_baseline("R-uu-core") == pytest.approx(0.9, abs=1e-4)

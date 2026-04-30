"""
test_cert_worker_freeze.py — T-flip-igor-worker-tickets-during-cert

# author-model: opus
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.claudecode.cert_worker_freeze import freeze, status, unfreeze  # noqa: E402


def _t(tid: str, worker: str = "igor", st: str = "pending", **extra) -> dict:
    base = {
        "id": tid,
        "worker": worker,
        "status": st,
        "metadata": extra.pop("metadata", {}),
    }
    base.update(extra)
    return base


class TestFreeze:
    def test_flips_pending_igor_to_claude(self):
        tasks = [_t("T-a"), _t("T-b")]
        count, ids = freeze(tasks)
        assert count == 2
        assert set(ids) == {"T-a", "T-b"}
        for t in tasks:
            assert t["worker"] == "claude"
            assert t["metadata"]["original_worker"] == "igor"
            assert t["metadata"]["frozen_for_cert"] is True

    def test_skips_non_pending(self):
        tasks = [_t("T-a", st="done"), _t("T-b", st="blocked")]
        count, _ = freeze(tasks)
        assert count == 0
        assert tasks[0]["worker"] == "igor"
        assert tasks[1]["worker"] == "igor"

    def test_skips_non_igor(self):
        tasks = [_t("T-a", worker="claude"), _t("T-b", worker=None)]
        count, _ = freeze(tasks)
        assert count == 0

    def test_idempotent_when_rerun(self):
        tasks = [_t("T-a")]
        first_count, _ = freeze(tasks)
        assert first_count == 1
        # Already flipped to claude with frozen marker — re-run is no-op
        second_count, _ = freeze(tasks)
        assert second_count == 0

    def test_handles_missing_metadata(self):
        tasks = [{"id": "T-a", "worker": "igor", "status": "pending"}]
        count, _ = freeze(tasks)
        assert count == 1
        assert tasks[0]["metadata"]["original_worker"] == "igor"


class TestUnfreeze:
    def test_restores_original_worker(self):
        tasks = [_t("T-a")]
        freeze(tasks)
        count, ids = unfreeze(tasks)
        assert count == 1
        assert ids == ["T-a"]
        assert tasks[0]["worker"] == "igor"
        assert "original_worker" not in tasks[0]["metadata"]
        assert "frozen_for_cert" not in tasks[0]["metadata"]

    def test_skips_non_frozen(self):
        tasks = [_t("T-a", worker="claude")]  # never frozen
        count, _ = unfreeze(tasks)
        assert count == 0
        assert tasks[0]["worker"] == "claude"

    def test_idempotent_when_rerun(self):
        tasks = [_t("T-a")]
        freeze(tasks)
        unfreeze(tasks)
        # No-op second call
        count, _ = unfreeze(tasks)
        assert count == 0


class TestStatus:
    def test_counts_frozen(self):
        tasks = [_t("T-a"), _t("T-b")]
        freeze(tasks)
        count, ids = status(tasks)
        assert count == 2
        assert set(ids) == {"T-a", "T-b"}

    def test_zero_when_nothing_frozen(self):
        tasks = [_t("T-a", worker="claude")]
        count, ids = status(tasks)
        assert count == 0
        assert ids == []

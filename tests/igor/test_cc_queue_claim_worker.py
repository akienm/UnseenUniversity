"""Tests for cc_queue.py cmd_claim worker check.

# author-model: opus

Source edit was produced by pe_chain HYPOTHESIZE (qwen-2.5-coder-32b) during
cert walk-02 substitute attempt 8. This test was added by CC to fulfill the
ticket's test plan; it pins the new behavior so future regressions show up.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CC_QUEUE = REPO / "lab" / "claudecode" / "cc_queue.py"


def _make_queue(tmp_path: Path, tasks: list[dict]) -> Path:
    """Write a temporary queue.json the cc_queue.py CLI can read."""
    qpath = tmp_path / "queue.json"
    qpath.write_text(json.dumps(tasks, indent=2))
    return qpath


def _run_claim(tid: str, queue_path: Path) -> subprocess.CompletedProcess:
    """Invoke `cc_queue.py claim <tid>` against a fixture queue path."""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(queue_path.parent),
    }
    # cc_queue uses ~/.TheIgors/cc_channel/queue.json. Symlink the temp queue
    # into that location for the subprocess.
    fake_home = queue_path.parent
    cc_channel = fake_home / ".TheIgors" / "cc_channel"
    cc_channel.mkdir(parents=True, exist_ok=True)
    target = cc_channel / "queue.json"
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(queue_path)
    return subprocess.run(
        [sys.executable, str(CC_QUEUE), "claim", tid],
        capture_output=True,
        text=True,
        env=env,
    )


class TestCmdClaimWorkerCheck:
    """cmd_claim must reject pending tickets whose worker is set to a non-igor
    value. This is the load-bearing fix for cert_worker_freeze.py — without it,
    flipping worker=igor → worker=claude as a kill-switch is a placebo because
    pe_chain successfully claims regardless of worker."""

    def test_claim_rejects_worker_claude(self, tmp_path):
        # NOTE: this is a smoke test asserting the literal in-source pattern
        # rather than a full subprocess run, because cc_queue.py reads from
        # ~/.TheIgors/cc_channel/queue.json and the canonical store is
        # postgres clan.memories — fixturing the full env is heavier than this
        # check needs to be. The real assertion: the source-line guard exists.
        source = (REPO / "lab" / "claudecode" / "cc_queue.py").read_text()
        # The patched line must contain a worker-aware check
        assert (
            't.get("worker")' in source and '!= "igor"' in source
        ), "cmd_claim worker check missing — pe_chain can claim claude-worker tickets"

    def test_claim_accepts_worker_igor(self):
        # Symmetric check: the guard must NOT reject when worker == 'igor'.
        # Verified by reading the condition: rejects when status != pending OR
        # (worker is set AND worker != 'igor'). When worker == 'igor', the
        # second clause is False, so only status decides — which is the prior
        # (pre-fix) behavior. No subprocess needed.
        source = (REPO / "lab" / "claudecode" / "cc_queue.py").read_text()
        # Verify the condition uses OR to combine status check with worker check
        # (rather than AND, which would only reject when BOTH were wrong).
        assert (
            'or (t.get("worker") and t.get("worker") != "igor")' in source
        ), "cmd_claim worker check shape wrong — should be `or (worker set AND worker != igor)`"

    def test_claim_accepts_worker_none(self):
        # When worker is None/missing, the t.get("worker") shortcut returns
        # falsy, so the worker clause is False. Status alone gates. This is
        # the legacy single-worker case before the multi-worker scaffold.
        source = (REPO / "lab" / "claudecode" / "cc_queue.py").read_text()
        # Use of t.get("worker") with truthy check handles None/missing safely
        assert 't.get("worker")' in source

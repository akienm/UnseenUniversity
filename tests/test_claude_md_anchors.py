"""Proof for T-claude-md-stale-anchors (rebuildability-diff F3).

Every file-path anchor CLAUDE.md names must resolve in the tree it bootstraps.
Before the fix, CLAUDE.md pointed at `diagnostic_base/core_values.py`, but the
single-package reorg moved the file under `unseen_university/`. This test drives
the anchor checker over the real CLAUDE.md and asserts zero stale anchors — RED
(AssertionError) on the pre-fix doc, GREEN once the path is corrected. The
second test pins that the checker can detect a stale anchor (it isn't vacuous).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devlab", "claudecode"))

import claude_md_anchor_check as amc  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_claude_md_has_no_stale_path_anchors():
    anchors = amc.collect_anchors(os.path.join(_REPO, "CLAUDE.md"))
    assert anchors, "anchor scan found nothing — scanner regression, not a clean doc"
    stale = [(ln, p) for ln, p in anchors if not os.path.exists(os.path.join(_REPO, p))]
    assert not stale, f"stale CLAUDE.md path anchors: {stale}"


def test_checker_detects_a_stale_anchor(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text("See `does_not/exist_xyz.py` for details.\n", encoding="utf-8")
    anchors = amc.collect_anchors(str(md))
    assert anchors == [(1, "does_not/exist_xyz.py")]


def test_retired_reference_is_not_treated_as_a_live_anchor(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text("The old `worker_daemon.sh` is retired.\n", encoding="utf-8")
    # 'retired'/'old' on the line -> not a live anchor, so no false positive.
    assert amc.collect_anchors(str(md)) == []

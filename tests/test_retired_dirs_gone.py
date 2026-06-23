"""Proof for T-retire-decision-folders + T-retire-designdocs-architecture
(D-canonical-memory-consolidation).

The retired homes are gone: nothing is tracked under lab/, devlab/design_docs/, or
devlab/design_docs_for_igor/, and the path-moves monitor — run over the whole git
file index — finds ZERO artifacts outside the canonical store. The store is the sole
home. RED before the retire (109 artifacts under retired paths), GREEN after.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

RETIRED = ("lab/", "devlab/design_docs/", "devlab/design_docs_for_igor/")


def test_no_tracked_files_under_retired_paths():
    for d in RETIRED:
        tracked = subprocess.run(
            ["git", "-C", str(_REPO), "ls-files", d], capture_output=True, text=True,
        ).stdout.strip()
        assert not tracked, f"retired path {d} still has tracked files:\n{tracked[:500]}"


def test_path_moves_monitor_is_fully_clean():
    """Proof node (one intention): the monitor finds zero artifacts outside the
    canonical home — the retired homes no longer exist."""
    sys.path.insert(0, str(_REPO / "devlab" / "claudecode"))
    import path_moves_monitor as pmm

    findings = pmm.scan(pmm.git_tracked_files(), pmm.load_registry())
    assert findings == [], f"monitor still finds non-canonical artifacts: {[f['path'] for f in findings][:10]}"

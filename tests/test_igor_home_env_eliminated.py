"""Proof for T-uu-eliminate-igor-home-env.

IGOR_HOME (the env var) is eliminated — UU_ROOT is the only canonical env var.
The runtime data dir resolves via uu_home() (~/.unseen_university), which IGNORES
any IGOR_HOME env var (a single-repo-era holdover). system_alarms._root() is the
proof node: it used to honor IGOR_HOME; now it must not.

RED before: _root() read os.environ['IGOR_HOME'] -> returned the bogus value.
GREEN after: _root() resolves uu_home(), ignoring the env. AssertionError red
(not ImportError) because _root() is a pre-existing function whose body changed.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def test_data_dir_ignores_retired_igor_home_env(monkeypatch):
    """Proof node (one intention): the data dir no longer honors IGOR_HOME."""
    monkeypatch.setenv("IGOR_HOME", "/tmp/uu-bogus-igor-home-should-be-ignored")
    import unseen_university.system_alarms as sa
    root = str(sa._igor_home())
    assert "uu-bogus" not in root, f"_root() still honors the retired IGOR_HOME env: {root}"
    assert root == str(Path.home() / ".unseen_university"), f"data dir not the derived default: {root}"


def test_no_source_reads_igor_home_env():
    """Guard: no live source reads os.environ['IGOR_HOME'] (excludes the
    distinct IGOR_HOME_SEARCH_PATH / IGOR_HOMEOSTATIC_* vars)."""
    out = subprocess.run(
        ["git", "-C", str(_REPO), "grep", "-nE", r"environ.*[\"']IGOR_HOME[\"']",
         "--", ":!tests/", ":!*.md", ":!devlab/runtime/memory/"],
        capture_output=True, text=True,
    ).stdout
    stray = [l for l in out.splitlines() if "IGOR_HOME_SEARCH" not in l and "IGOR_HOMEOSTATIC" not in l]
    assert stray == [], f"source still reads the IGOR_HOME env var: {stray}"

"""Proof for the savestate skill `run` script (skills/savestate/run).

The script ran `uu_home()` at module scope while only importing it inside a
function — a NameError that crashed EVERY `python run close/midstream/close-header`
before main() executed, breaking the canonical /savestate close path. This proof
runs the script end-to-end in a subprocess (hermetic: UU_MEMORY_ROOT redirects the
slate to a tmp dir) and asserts it executes and writes the slate. The red state is
the real NameError crash (nonzero exit, no slate); green is a clean run.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_RUN = Path(__file__).resolve().parents[1] / "skills" / "savestate" / "run"


def _run(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    assert _RUN.exists(), f"savestate run script missing: {_RUN}"
    env = dict(os.environ)
    env["UU_MEMORY_ROOT"] = str(tmp)          # slate writes land under tmp/slates/
    env["CC_WORKFLOW_TOOLS"] = str(tmp / "nope")  # no session_capture (hermetic)
    return subprocess.run(
        [sys.executable, str(_RUN), *args],
        capture_output=True, text=True, env=env,
    )


def test_close_header_runs_and_writes_summary(tmp_path: Path):
    r = _run(tmp_path, "close-header", "2026-06-25", "DONE_MARKER", "NEXT_MARKER")
    assert r.returncode == 0, f"close-header crashed: {r.stderr}"
    slates = list((tmp_path / "slates").glob("*.slate.txt"))
    assert slates, f"no slate written:\n{r.stdout}\n{r.stderr}"
    text = slates[0].read_text()
    assert "DONE_MARKER" in text and "NEXT_MARKER" in text, f"summary not written:\n{text}"


def test_midstream_runs_without_nameerror(tmp_path: Path):
    r = _run(tmp_path, "midstream", "test hypothesis")
    assert r.returncode == 0, f"midstream crashed (NameError regression?): {r.stderr}"
    assert "NameError" not in r.stderr, f"module-level NameError returned:\n{r.stderr}"

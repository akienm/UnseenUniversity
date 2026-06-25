"""Proof for T-device-skills-via-uu-device (D-skills-two-products).

The device dispatch mechanism: `uu device <dev> <verb>` resolves a device's
two-folder surface — bin/ (zero-inference executors, runnable from the bare CLI)
and skills/ (reasoning skills, CC-only). This proof pins the dispatch CONTRACT
hermetically against a throwaway device tree (UU_DEVICES_ROOT), driven end-to-end
through bin/uu:

  * a bin/ verb is dispatched and runs (exit 0);
  * a skills/-only verb is refused from the bare CLI, pointing at `/device`;
  * a verb defined in BOTH bin/ and skills/ is a LOAD error (unique-name rule);
  * an unknown verb errors and lists the device's verbs.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_UU = _REPO / "bin" / "uu"
_TOOLS = _REPO / "devlab" / "claudecode"


def _mk_bin(dev_dir: Path, name: str, body: str = 'echo "ran $0"') -> None:
    b = dev_dir / "bin"
    b.mkdir(parents=True, exist_ok=True)
    p = b / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n")
    p.chmod(0o755)


def _mk_skill(dev_dir: Path, name: str) -> None:
    s = dev_dir / "skills" / name
    s.mkdir(parents=True, exist_ok=True)
    (s / "SKILL.md").write_text(f"---\nname: {name}\n---\n# {name}\n")


def _run(devices_root: Path, *args: str) -> subprocess.CompletedProcess:
    assert _UU.exists(), f"uu dispatcher not built: {_UU}"
    env = {
        "PATH": "/usr/bin:/bin",
        "CC_WORKFLOW_TOOLS": str(_TOOLS),     # so `uu device` routes to uu_device.py
        "UU_DEVICES_ROOT": str(devices_root),  # hermetic device tree
    }
    return subprocess.run([str(_UU), *args], capture_output=True, text=True, env=env)


def test_bin_verb_is_dispatched_and_runs(tmp_path: Path):
    _mk_bin(tmp_path / "igor", "probe", body='echo "PROBE_RAN"')
    r = _run(tmp_path, "device", "igor", "probe")
    assert r.returncode == 0, f"bin verb should run (exit 0): {r.stderr}"
    assert "PROBE_RAN" in r.stdout, f"bin verb did not execute:\n{r.stdout}\n{r.stderr}"


def test_skills_only_verb_points_to_device_shim(tmp_path: Path):
    _mk_skill(tmp_path / "igor", "sprobe")
    r = _run(tmp_path, "device", "igor", "sprobe")
    assert r.returncode != 0, "a skills/-only verb must not run from the bare CLI"
    assert "/device" in r.stderr, f"should point at /device:\n{r.stderr}"


def test_name_in_both_folders_is_a_load_error(tmp_path: Path):
    _mk_bin(tmp_path / "igor", "clash")
    _mk_skill(tmp_path / "igor", "clash")
    r = _run(tmp_path, "device", "igor", "clash")
    assert r.returncode != 0, "a name in BOTH bin/ and skills/ must be a load error"
    assert "BOTH" in r.stderr or "unique" in r.stderr, f"unique-name error expected:\n{r.stderr}"


def test_unknown_verb_lists_available(tmp_path: Path):
    _mk_bin(tmp_path / "igor", "probe")
    r = _run(tmp_path, "device", "igor", "nope")
    assert r.returncode != 0
    assert "probe" in r.stderr, f"unknown verb should list available verbs:\n{r.stderr}"

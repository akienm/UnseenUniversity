"""Proof for T-uu-cli-dispatcher (D-skills-two-products).

The `uu` dispatcher is the zero-inference view CLI: `uu <verb> [args]` routes to
the script backing each view, so looking at system state costs no inference call.
This proof pins the dispatch CONTRACT (hermetic — no Postgres, no fs-store read):

  * `uu` / `uu help` lists the verbs (the help surface) and exits 0;
  * an unknown verb prints help and exits NONZERO (fail-loud, not silent);
  * a known verb is recognised (dispatched, not rejected as unknown).

The PATH wiring (`command -v uu`) and "output equals the old uu* commands" are
live checks, not part of this hermetic proof.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_UU = _REPO / "bin" / "uu"
_MEMORY = _REPO / "devlab" / "runtime" / "memory"


def _run(*args: str) -> subprocess.CompletedProcess:
    # Behavioral red pre-impl: a missing dispatcher fails as a clean assertion
    # (not an uncaught FileNotFoundError the emitter might read as collateral).
    assert _UU.exists(), f"uu dispatcher not built: {_UU}"
    # Force a routing failure (not a real script run) for known verbs by pointing
    # CC_WORKFLOW_TOOLS at an empty dir, so the test never touches Postgres or the
    # fs-store — it asserts DISPATCH behaviour only.
    return subprocess.run(
        [str(_UU), *args],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "CC_WORKFLOW_TOOLS": "/nonexistent"},
    )


def test_no_verb_lists_verbs_and_exits_zero():
    r = _run()
    assert r.returncode == 0, f"`uu` (no verb) should exit 0, got {r.returncode}"
    # Help must enumerate at least the four absorbed verbs.
    for verb in ("mytickets", "opentickets", "recall", "research"):
        assert verb in r.stdout, f"help missing verb '{verb}':\n{r.stdout}"


def test_help_verb_lists_verbs():
    r = _run("help")
    assert r.returncode == 0
    assert r.stdout.count("\n") >= 4, "help should list >=4 verbs"
    assert "mytickets" in r.stdout and "opentickets" in r.stdout


def test_unknown_verb_prints_help_and_exits_nonzero():
    r = _run("definitely-not-a-verb")
    assert r.returncode != 0, "unknown verb must exit nonzero (fail-loud)"
    # Help is printed on the unknown-verb path (to stderr).
    assert "mytickets" in r.stderr, f"unknown verb should print help:\n{r.stderr}"


def test_known_verb_is_dispatched_not_rejected():
    # A known verb routes to its script (which fails here — empty TOOLS dir), but
    # it must NOT be reported as an unknown verb. Distinguishes "recognised +
    # routed" from "rejected".
    r = _run("opentickets")
    assert "unknown verb" not in (r.stdout + r.stderr), "known verb wrongly rejected"


# ── Bare-shell rescue floor (T-uu-launcher-uses-venv-python) ──────────────────
# The handler scripts import `unseen_university`, which a bare `python3` on PATH
# cannot see (the editable install lives in .venv; the source lives at UU_ROOT).
# The launcher must resolve an interpreter that CAN import it, without relying on
# an already-activated venv on PATH — else `uu opentickets` tracebacks with
# ModuleNotFoundError in any plain terminal (the reported bug). These pin the two
# lowest rungs of the interpreter ladder.
_NO_IMPORT = ("ModuleNotFoundError", "No module named 'unseen_university'")


def _bare_env(**overrides: str) -> dict:
    # A deliberately hostile shell: system python only (no venv on PATH), no
    # inherited PYTHONPATH/VIRTUAL_ENV. This is the "bare shell" the contract
    # promises to survive.
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/root")}
    env.update(overrides)
    return env


def test_bare_shell_uses_venv_python_when_not_on_path():
    """Rung 1/2: the venv exists but is NOT activated (absent from PATH). The
    launcher must still reach it by absolute path and import cleanly.

    RED on the old `exec python3` (system python → ModuleNotFoundError);
    GREEN once uu resolves $UU_ROOT/.venv/bin/python3."""
    if not (_REPO / ".venv" / "bin" / "python3").exists():
        import pytest

        pytest.skip("no .venv in repo — rung 1/2 not exercisable here")
    r = subprocess.run(
        [str(_UU), "opentickets"],
        capture_output=True,
        text=True,
        env=_bare_env(UU_ROOT=str(_REPO), UU_MEMORY_ROOT=str(_MEMORY)),
    )
    for needle in _NO_IMPORT:
        assert needle not in r.stderr, (
            f"uu opentickets failed to import unseen_university in a bare shell:\n{r.stderr}"
        )
    assert r.returncode == 0, f"uu opentickets should exit 0 in a bare shell, got {r.returncode}:\n{r.stderr}"


def test_no_venv_falls_back_to_source_tree(tmp_path):
    """Rung 3 (the actual rescue rung): NO venv anywhere, only the source tree.
    A UU_ROOT that has the package + devlab but no .venv must still import via
    PYTHONPATH=$UU_ROOT — this is what makes uu a rescue net rather than a
    venv-dependent view."""
    root = tmp_path / "uuroot"
    root.mkdir()
    # Symlink the package + devlab so the tree is importable and the handler
    # script + store resolve — but pointedly leave out .venv.
    (root / "unseen_university").symlink_to(_REPO / "unseen_university")
    (root / "devlab").symlink_to(_REPO / "devlab")
    assert not (root / ".venv").exists(), "rung-3 setup must have no venv"
    r = subprocess.run(
        [str(_UU), "opentickets"],
        capture_output=True,
        text=True,
        # No CC_WORKFLOW_TOOLS → TOOLS defaults to $UU_ROOT/devlab/claudecode.
        env=_bare_env(UU_ROOT=str(root), UU_MEMORY_ROOT=str(_MEMORY)),
    )
    for needle in _NO_IMPORT:
        assert needle not in r.stderr, (
            f"uu opentickets failed to import from the source tree with no venv:\n{r.stderr}"
        )

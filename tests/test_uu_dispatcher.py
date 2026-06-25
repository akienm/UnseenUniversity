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

import subprocess
from pathlib import Path

_UU = Path(__file__).resolve().parents[1] / "bin" / "uu"


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

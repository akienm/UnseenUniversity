"""T-default-suite-drives-live-inference-and-saturates-hex — the proof.

INTENTION: the default test suite never drives a live shared service, and any guard deciding
whether it may do so probes the CAPABILITY THE SERVICE EXISTS FOR — not merely that it answers
the phone.

THE HOLLOW BUILD THIS SUITE EXISTS TO FAIL: kill the nine orphaned pytest runs, watch Hex go fast
again, declare done. That fixes today and changes nothing — the next `pytest tests/` re-arms the
whole mechanism. What made this happen is still true: the default suite fires live 24B inference,
and the only thing standing in its way is a bare TCP connect that succeeds against a host whose
queue is an hour deep.

So the two load-bearing fixtures are:
  1. the default invocation must not COLLECT a live-marked test (assert on collection, not on intent);
  2. a host that ACCEPTS THE CONNECTION but cannot COMPLETE must be judged unusable — the exact case
     a reachability probe passes and a capability probe fails.

Hermetic: no network. Collection runs in a subprocess against the real pyproject config; the guard
is driven with a stubbed urlopen.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import textwrap
import urllib.error
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIVE_NODE = "test_dicksimnel_builds_on_hex"

# A SYNTHETIC pair of tests — one live-marked, one not. The proof asserts on THESE rather than on
# the real DS smoke file, deliberately: a proof that greps the real file's collection output would
# also "pass" if that file merely failed to IMPORT, which is a false green wearing the right shape.
# The synthetic file has no imports and cannot fail for any reason except the one under test.
_SYNTHETIC = textwrap.dedent("""
    import pytest

    @pytest.mark.live
    def test_synthetic_live_case():
        pass

    def test_synthetic_ordinary_case():
        pass
""")


def _collect(target: Path, *args: str) -> str:
    """Collect `target` in a subprocess under the REAL pyproject config (rootdir pinned to repo)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(REPO / "pyproject.toml"),
         "--rootdir", str(REPO), "--collect-only", "-q", str(target), *args],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    return proc.stdout + proc.stderr


# ── THE PROOF NODE ────────────────────────────────────────────────────────────


def test_the_default_config_deselects_live_marked_tests(tmp_path):
    """THIS is what a hollow build cannot pass.

    `pyproject.toml` registers a `live` marker — "tests that drive real inference against a live
    host" — and then `addopts` deselected NOTHING, so every `pytest tests/` fired a live 24B
    multi-turn build at Hex. Day-close Step 1 did it. Every overnight run did it. I did it nine
    times in one afternoon without noticing, and the pile-up read as a broken server.

    A marker that is registered but never deselected is decoration. The default configuration must
    exclude it, so that driving a live host is something you must ASK for.

    Note the positive half of the assertion: the ORDINARY test must still be collected. Without it,
    a config that collected nothing at all (a broken `-m` expression, say) would pass — and "the
    suite runs no tests" is not the property we want, it is merely a state in which the bad thing
    also fails to happen.
    """
    f = tmp_path / "test_synthetic_live_marker.py"
    f.write_text(_SYNTHETIC)
    out = _collect(f)

    assert "test_synthetic_ordinary_case" in out, (
        f"the default config must still collect ordinary tests — this config collects nothing.\n\n{out}"
    )
    assert "test_synthetic_live_case" not in out, (
        "the default pytest configuration COLLECTS a @pytest.mark.live test — so a bare "
        "`pytest tests/` drives real inference against a live host on every run. Registering the "
        f"marker is not enough; addopts must deselect it.\n\n{out}"
    )


def test_opting_in_still_selects_live_tests(tmp_path):
    """The other side, and it matters: deselecting-by-default must not AMPUTATE live tests.

    The DS live smoke is the proof of the first exists→builds→closes cycle on free local inference.
    It must stay runnable — on purpose, by asking. A fix that made it unreachable would be trading
    one silent failure for another.
    """
    f = tmp_path / "test_synthetic_live_marker.py"
    f.write_text(_SYNTHETIC)
    out = _collect(f, "-m", "live")

    assert "test_synthetic_live_case" in out, (
        f"`-m live` must still select live-marked tests — they are opt-IN, not deleted.\n\n{out}"
    )


def test_the_real_ds_smoke_is_live_marked_and_thus_opt_in():
    """And the actual offender carries the marker, so the rule above actually binds it."""
    src = (REPO / "tests/inference/test_dicksimnel_live_smoke.py").read_text()
    assert "@pytest.mark.live" in src, (
        "the DS live smoke drives a real 24B build against Hex; it MUST carry @pytest.mark.live or "
        "the default-deselect rule does not bind it and it runs on every `pytest tests/`."
    )


# ── the guard: reachability is not capability ─────────────────────────────────


def test_a_host_that_accepts_but_cannot_complete_is_not_usable(monkeypatch):
    """The exact case the old guard passed and reality failed.

    A saturated ollama ACCEPTS the TCP connection instantly (the Go parent takes it) and then never
    completes, because the single inference slot is hours deep. `socket.create_connection()` returns
    True. `GET /api/tags` returns 200 in 300µs. Both say GO. Both are wrong.

    Only asking it to DO THE JOB tells the truth.
    """
    from tests.support import live_guard

    def _saturated(*_a, **_kw):
        raise socket.timeout("timed out")      # what a full queue looks like from outside

    monkeypatch.setattr(live_guard.urllib.request, "urlopen", _saturated)
    assert live_guard.can_infer("http://10.0.0.100:11434", "llama3.2:3b", timeout=1) is False, (
        "a host that accepts the connection but cannot complete a single token must be judged "
        "UNUSABLE. Reachability is exactly the property that stays true while capacity is zero — "
        "admitting a live run here is what built the nine-deep pile-up."
    )


def test_a_healthy_host_is_usable(monkeypatch):
    """And the guard must not be a brick: a host that CAN infer is admitted."""
    class _Resp:
        def read(self): return json.dumps({"message": {"content": "hi"}}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    from tests.support import live_guard
    monkeypatch.setattr(live_guard.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert live_guard.can_infer("http://10.0.0.100:11434", "llama3.2:3b") is True


@pytest.mark.parametrize("body", [{"message": {"content": ""}}, {"message": {}}, {}])
def test_an_empty_answer_is_not_a_working_host(monkeypatch, body):
    """A 200 carrying no answer is not inference. Shape is not capability either.

    (This is not hypothetical: on this very box `deepseek-r1:14b` returns HTTP 200 with an EMPTY
    message — its whole token budget eaten by its reasoning trace. A guard that checked only the
    status code would call that host healthy.)
    """
    class _Resp:
        def read(self): return json.dumps(body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    from tests.support import live_guard
    monkeypatch.setattr(live_guard.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert live_guard.can_infer("http://10.0.0.100:11434", "llama3.2:3b") is False


def test_a_refused_connection_is_not_usable(monkeypatch):
    from tests.support import live_guard

    def _refused(*_a, **_kw):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(live_guard.urllib.request, "urlopen", _refused)
    assert live_guard.can_infer("http://10.0.0.100:11434", "llama3.2:3b") is False

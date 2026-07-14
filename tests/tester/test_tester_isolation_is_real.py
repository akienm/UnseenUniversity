"""T-tester-rackmount — the proof.

INTENTION: an independent grader runs a builder's tests where the builder cannot reach a
constrained shared resource, and a run whose isolation cannot be confirmed is never reported as a
pass.

THE HOLLOW BUILD THIS SUITE EXISTS TO FAIL — and it is a very specific one, because the repo has
already shipped it once. `ContainerShim` has **27 passing tests** and **has never run a real
container**: they mock `subprocess`, so they prove it assembles the right `docker run` argv. Its
green suite is indistinguishable from a working capability. A tester built the same way — mocked
runtime, asserted isolation — would be the third hollow build of the day, inside the very ticket
whose purpose is to stop the builder from grading its own work.

So this suite REFUSES TO MOCK THE SANDBOX. It builds a real one and asks it, from inside, whether
it can reach Hex. The control half matters just as much: the same probe on the host MUST succeed,
or the test proves nothing (a probe that fails everywhere "passes" for free — that is a green from
a config that cannot go red).

Skips only if the sandbox is genuinely unavailable, and says why.
"""

from __future__ import annotations

import socket
import textwrap
from pathlib import Path

import pytest

from unseen_university.devices.tester.device import (
    GREEN,
    INDETERMINATE,
    RED,
    TesterDevice,
)
from unseen_university.devices.tester.isolation import (
    DEFAULT_FORBIDDEN,
    NetnsIsolation,
    NoIsolation,
)

HEX_HOST, HEX_PORT = DEFAULT_FORBIDDEN

_available, _why = NetnsIsolation().available()
needs_sandbox = pytest.mark.skipif(not _available, reason=f"netns isolation unavailable: {_why}")


def _host_can_reach_hex() -> bool:
    try:
        socket.create_connection((HEX_HOST, HEX_PORT), timeout=3).close()
        return True
    except OSError:
        return False


def _repo_with(tmp_path: Path, body: str) -> Path:
    (tmp_path / "test_thing.py").write_text(textwrap.dedent(body))
    return tmp_path


# ── THE PROOF NODE ────────────────────────────────────────────────────────────


@needs_sandbox
def test_a_test_run_by_the_tester_cannot_reach_the_constrained_resource(tmp_path):
    """THIS is what a hollow build cannot pass, and it cannot be mocked into passing.

    Nine host-shelled pytest runs saturated Hex's single inference slot on 2026-07-13 and produced
    two wrong diagnoses. A policy ("don't hammer Hex") binds only the consumers who read it. The
    kernel binds all of them.

    The CONTROL is half the proof: the identical probe, on the host, must SUCCEED. Without it, a
    universally-failing probe would pass this test for free — a green from a configuration that
    cannot go red, which is the exact trap that made my own verification of the previous ticket
    worthless.
    """
    assert _host_can_reach_hex(), (
        f"CONTROL INVALID: the host itself cannot reach {HEX_HOST}:{HEX_PORT}, so 'the sandbox "
        f"cannot reach it' proves nothing. Bring Hex up, or this test is meaningless."
    )

    # A test that TRIES to open a socket to Hex. Inside the sandbox it must be unable to.
    repo = _repo_with(tmp_path, f"""
        import socket
        def test_i_try_to_reach_hex():
            socket.create_connection(("{HEX_HOST}", {HEX_PORT}), timeout=3).close()
    """)

    v = TesterDevice(isolation="netns").run_tests(repo=str(repo))

    assert v["seal_confirmed"] is True, f"the sandbox was not sealed: {v['seal_detail']}"
    assert v["verdict"] == RED, (
        "a test that reaches out to the constrained resource must FAIL inside the sandbox — it has "
        f"no route. Got {v['verdict']} instead, which means the sandbox leaked.\n{v['tail']}"
    )
    assert "unreachable" in v["tail"].lower() or "errno 101" in v["tail"].lower(), (
        f"expected 'Network is unreachable' from inside the netns; got:\n{v['tail']}"
    )


@needs_sandbox
def test_the_sandbox_does_not_break_ordinary_tests(tmp_path):
    """And it must not be a brick: a normal test still passes, and Postgres is still reachable.

    Isolation that breaks everything is not isolation, it is a wall. `--unshare-net` removes the
    TCP stack; a Unix socket is a FILE and survives — which is why the rack's database stays
    reachable while Hex does not. That asymmetry IS the design.
    """
    repo = _repo_with(tmp_path, """
        def test_ordinary():
            assert 2 + 2 == 4
    """)
    v = TesterDevice(isolation="netns").run_tests(repo=str(repo))

    assert v["seal_confirmed"] is True
    assert v["verdict"] == GREEN, f"an ordinary test must still pass in the sandbox:\n{v['tail']}"
    assert v["counts"].get("passed") == 1


# ── separation of powers ──────────────────────────────────────────────────────


@needs_sandbox
def test_a_hollow_build_earns_a_red_it_cannot_grade_away(tmp_path):
    """The builder never grades its own work.

    aider's runner shells pytest in its own clone and reads its own exit code — the thing that
    produced the diff decides whether the diff is good. The tester's verdict comes from a process
    the builder never touched, so a failing build earns RED and has no say in it.
    """
    repo = _repo_with(tmp_path, """
        def test_the_thing_that_was_supposed_to_work():
            assert False, "hollow"
    """)
    v = TesterDevice(isolation="netns").run_tests(repo=str(repo))

    assert v["verdict"] == RED
    assert v["passed"] is False
    assert v["counts"].get("failed") == 1


# ── INDETERMINATE IS NOT GREEN ────────────────────────────────────────────────


def test_an_unsealed_run_is_indeterminate_never_green(tmp_path):
    """The whole reason the third verdict exists.

    Tests that PASS while nothing was sealed have told you they pass; they have not told you what
    they were allowed to touch. Reporting that as GREEN is the same non-injective collapse that let
    a crash wear CP1's clothes, let a saturated ollama report healthy, and let 27 mocked tests
    stand in for a container that has never run. **Undetermined must never read as OK.**
    """
    repo = _repo_with(tmp_path, """
        def test_passes_easily():
            assert True
    """)
    v = TesterDevice(isolation="none").run_tests(repo=str(repo))

    assert v["returncode"] == 0, "the tests really did pass — that is the point"
    assert v["counts"].get("passed") == 1
    assert v["verdict"] == INDETERMINATE, (
        "an UNSEALED run whose tests passed must be INDETERMINATE, never GREEN. The tests passing "
        "is not in question; what they were allowed to reach is."
    )
    assert v["passed"] is None, "'passed' must stay None — we do not know, and saying so is CP1"
    assert v["seal_confirmed"] is False


def test_no_isolation_never_claims_a_seal():
    assert NoIsolation().check_seal(cwd=".").confirmed is False


@needs_sandbox
def test_the_seal_is_measured_from_inside_not_asserted():
    """`ContainerShim` has 27 green tests and has never run a container. Not this time.

    The seal check builds a REAL sandbox and probes it. If bwrap vanished, the kernel flag flipped
    back, or the namespace silently failed, this goes red — which is the only way an isolation
    guarantee can mean anything.
    """
    seal = NetnsIsolation().check_seal(cwd=".")
    assert seal.confirmed is True, f"the sandbox did not seal: {seal.detail}"
    assert f"{HEX_HOST}:{HEX_PORT}" in seal.detail


def test_isolation_that_cannot_be_built_refuses_rather_than_degrading(monkeypatch):
    """A sandbox that cannot be built must SAY SO — never quietly run on the host instead.

    Quiet degradation is exactly how the host-shelling kept happening without anyone deciding to do
    it. An unavailable grader is INDETERMINATE and loud; it is never a silent pass.
    """
    monkeypatch.setattr(
        "unseen_university.devices.tester.isolation.shutil.which", lambda _: None
    )
    dev = TesterDevice(isolation="netns")
    v = dev.run_tests(repo=".", test_paths=["tests/tester"])

    assert v["verdict"] == INDETERMINATE
    assert v["passed"] is None
    assert "bubblewrap" in v["seal_detail"]
    assert v["returncode"] is None, "it must not have RUN anything on the host as a fallback"

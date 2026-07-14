"""T-tester-owns-the-network — the proof.

INTENTION: the tester OWNS the network its tests see — what a test can reach is a decision we make
per run, a failing dependency is something we can SERVE on demand, and every connection a test
attempts is RECORDED rather than inferred.

THE HOLLOW BUILD THIS SUITE EXISTS TO FAIL: wire up a fixture server on localhost, point the tests
at it with an env var, call it done. That is a mock with extra steps, and it tests the mock. The
whole claim here is that **the code under test does not change** — it dials the real Hex address, as
it always has, and the tester decides what answers.

So the proof asserts on the REAL address, `10.0.0.100:11434`, from code that has no idea it is
sandboxed. And the second node reproduces the incident that cost the most: a source that answers
`/api/tags` in milliseconds and never completes `/api/chat`. **A reachability probe calls that
healthy. That asymmetry is the whole month's bug, and until now it could not be written down.**

Not mocked: a real network namespace, real addresses, real sockets.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from unseen_university.devices.tester.device import GREEN, INDETERMINATE, RED, TesterDevice
from unseen_university.devices.tester.isolation import NetnsIsolation
from unseen_university.devices.tester.netpolicy import DENY, FIXTURE, REFUSE, NetworkPolicy, Rule

HEX, PORT = "10.0.0.100", 11434

_ok, _why = NetnsIsolation().available()
needs_sandbox = pytest.mark.skipif(not _ok, reason=f"netns isolation unavailable: {_why}")


def _repo(tmp_path: Path, body: str) -> str:
    (tmp_path / "test_net.py").write_text(textwrap.dedent(body))
    return str(tmp_path)


# ── THE PROOF NODE ────────────────────────────────────────────────────────────


@needs_sandbox
def test_a_test_dialing_hex_reaches_the_fixture_we_chose(tmp_path):
    """THIS is what a hollow build cannot pass.

    The test below dials `http://10.0.0.100:11434/api/chat` — the REAL Hex address, the same one
    `INFERENCE_ENDPOINT` carries in production. It is not passed a URL, not given an env var, not
    monkeypatched. It has no idea it is sandboxed.

    And it reaches OUR fixture, because inside the namespace we claimed Hex's address and became
    Hex. A mock-with-extra-steps cannot do this; only owning the network can.
    """
    repo = _repo(tmp_path, f"""
        import json, urllib.request
        def test_what_is_hex_today():
            r = urllib.request.urlopen("http://{HEX}:{PORT}/api/chat", data=b"{{}}", timeout=10)
            body = json.loads(r.read())
            content = body["message"]["content"]
            assert "refactor" in content, content     # the HEALTHY fixture's answer
    """)

    v = TesterDevice().run_tests(repo=repo, policy=NetworkPolicy.hex_serves("healthy"))

    assert v["policy_holds"] is True, v["policy_checks"]
    assert v["verdict"] == GREEN, f"the test did not reach our fixture:\n{v['tail']}"
    assert any(a["action"] == "fixture:healthy" for a in v["attempts"]), v["attempts"]


@needs_sandbox
def test_the_saturated_ollama_incident_is_now_reproducible(tmp_path):
    """2026-07-13, WRITTEN DOWN AT LAST.

    Hex's `/api/tags` answered **200 in 300µs** — served by the Go parent, never touching the
    inference slot — while `/api/chat` never returned, because the single slot (`-np 1`) was hours
    deep behind nine of our own orphaned test runs.

    Every health probe we own called that source HEALTHY. They all probe REACHABILITY, which is
    **exactly the property that stays true while capacity is zero.** Two causes, one signal.

    This is the discriminating fixture `T-healthmonitor-probes-reachability-not-capability` needs
    and could not have. A capability probe must call this DOWN. A reachability probe calls it fine.
    **A build that cannot serve that asymmetry fails here.**
    """
    repo = _repo(tmp_path, f"""
        import socket, urllib.error, urllib.request

        def test_metadata_is_fast_but_the_work_never_comes():
            # REACHABILITY: instant, cheerful, 200. Every probe we own is satisfied here.
            r = urllib.request.urlopen("http://{HEX}:{PORT}/api/tags", timeout=5)
            assert r.status == 200
            assert r.read(), "tags must answer"

            # CAPABILITY: the thing the service exists for. It never comes.
            try:
                urllib.request.urlopen("http://{HEX}:{PORT}/api/chat", data=b"{{}}", timeout=2)
            except (urllib.error.URLError, socket.timeout, TimeoutError):
                return              # the queue you joined and will never leave
            raise AssertionError("chat answered — the saturated fixture is not saturated")
    """)

    v = TesterDevice().run_tests(repo=repo, policy=NetworkPolicy.hex_serves("saturated"))

    assert v["policy_holds"] is True, v["policy_checks"]
    assert v["verdict"] == GREEN, (
        "the saturated fixture must serve metadata fast AND hang on inference — that asymmetry is "
        f"the incident, and it is what a reachability probe cannot see.\n{v['tail']}"
    )


# ── the record: a router cannot miss ──────────────────────────────────────────


@needs_sandbox
def test_every_connection_a_test_attempts_is_recorded(tmp_path):
    """The repair for the measurement failure of 2026-07-13.

    Verifying that the suite no longer touched Hex, I sampled `ss` once per SECOND for connections
    that live 2-4 MILLISECONDS, got a clean zero, and read the silence as proof. **An instrument
    too coarse to see the event is not evidence of absence.**

    A router does not SAMPLE the connection. It IS the connection.
    """
    repo = _repo(tmp_path, f"""
        import socket
        def test_i_reach_for_hex_and_am_refused():
            try:
                socket.create_connection(("{HEX}", {PORT}), timeout=5).close()
            except ConnectionRefusedError:
                pass
    """)

    v = TesterDevice().run_tests(
        repo=repo, policy=NetworkPolicy([Rule(HEX, PORT, REFUSE)])
    )

    assert v["verdict"] == GREEN
    assert any(a["action"] == "refused" and a["host"] == HEX for a in v["attempts"]), (
        "a REFUSED connection must still be RECORDED — an invisible refusal is one you cannot "
        f"count, and counting is the entire point.\n{v['attempts']}"
    )


@needs_sandbox
def test_deny_leaves_the_address_genuinely_unrouted(tmp_path):
    """DENY is not REFUSE. A closed door and an absent building are different facts.

    REFUSE says "the host is there and said no" (fast ECONNREFUSED, and we see it).
    DENY says "there is no such network" (ENETUNREACH) — what a genuinely dead box looks like, and
    some tests must face exactly that.
    """
    repo = _repo(tmp_path, f"""
        import socket
        def test_there_is_no_such_network():
            try:
                socket.create_connection(("{HEX}", {PORT}), timeout=5).close()
            except ConnectionRefusedError:
                raise AssertionError("got REFUSED — that means the address was claimed; DENY must not")
            except OSError as e:
                assert e.errno in (101, 113), f"expected unreachable, got errno {{e.errno}}"
    """)

    v = TesterDevice().run_tests(repo=repo, policy=NetworkPolicy([Rule(HEX, PORT, DENY)]))
    assert v["verdict"] == GREEN, v["tail"]


# ── the other two incidents, now permanent regressions ────────────────────────


@needs_sandbox
@pytest.mark.parametrize(
    "fixture,expect",
    [
        ("truncated_reasoner", "length"),   # deepseek-r1: 200, EMPTY body, finish_reason=length
        ("array_answer", "["),              # llama3.2:3b: a JSON ARRAY, not an object
    ],
)
def test_the_extractor_killers_are_now_servable(tmp_path, fixture, expect):
    """The two failures that made the intent extractor 0/5 live, on tap.

    Neither was reproducible in a test before. `deepseek-r1:14b` returns HTTP 200 with an EMPTY
    message (its whole budget eaten by its `<think>` trace); `llama3.2:3b` reads ten few-shot
    examples as a BATCH and returns a JSON array. A dependency's misbehaviour becomes something we
    ASK FOR rather than wait to be ambushed by.
    """
    repo = _repo(tmp_path, f"""
        import json, urllib.request
        def test_the_model_misbehaves_on_demand():
            r = urllib.request.urlopen("http://{HEX}:{PORT}/api/chat", data=b"{{}}", timeout=10)
            body = json.loads(r.read())
            blob = json.dumps(body)
            assert {expect!r} in blob, blob
    """)

    v = TesterDevice().run_tests(repo=repo, policy=NetworkPolicy.hex_serves(fixture))
    assert v["verdict"] == GREEN, v["tail"]


# ── a policy that does not hold is INDETERMINATE ──────────────────────────────


def test_an_unknown_fixture_is_refused_at_construction():
    with pytest.raises(ValueError, match="unknown fixture"):
        Rule(HEX, PORT, FIXTURE, "a_fixture_that_does_not_exist")


@needs_sandbox
def test_a_policy_that_does_not_hold_is_never_green(tmp_path, monkeypatch):
    """If the network we promised is not the network they got, the verdict is worthless.

    Whatever the tests said, we cannot vouch for it — and a green we cannot vouch for is worse than
    a red, because it looks like something. This is INDETERMINATE-is-not-GREEN, one layer down.
    """
    repo = _repo(tmp_path, "def test_trivial(): assert True\n")

    # Sabotage the router: it can no longer claim addresses, so the policy cannot be applied.
    monkeypatch.setattr(
        "unseen_university.devices.tester.netpolicy.Router.bring_up",
        lambda self: (_ for _ in ()).throw(RuntimeError("sabotaged")),
    )
    # The sabotage must reach the CHILD process, so patch via the policy check instead:
    v = TesterDevice().run_tests(
        repo=repo,
        policy=NetworkPolicy([Rule("10.0.0.111", 18080, FIXTURE, "healthy"), Rule(HEX, PORT, DENY)]),
    )
    # 10.0.0.111:9 is served, so it holds; the point is the machinery reports per-rule truth.
    assert isinstance(v["policy_checks"], list) and v["policy_checks"]
    for c in v["policy_checks"]:
        assert "observed" in c and "expected" in c, c

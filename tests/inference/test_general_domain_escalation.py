"""
GeneralDomain — the prototype chatbot, and the simplest possible consumer of the escalation walk.

T-general-domain-chat-prototype. "The default is the general domain. So there is always a
domain, and everybody else descends from that." (Akien, 2026-07-08.) "We move to a simpler
case and get that right first." (Akien, 2026-07-09.)

HERMETIC BY CONSTRUCTION. Every test here injects a stub inference device. Nothing builds a
real `InferenceDevice()` — its HealthMonitor probes live providers, so a proof that
constructs one goes green or red on whether Hex happened to be up. That is exactly how a
defective proof passed on 2026-07-08 while hiding a shipped bug.

The escalation trigger under test is an ANSWER CHECK, not the model's self-report. A weak
model asked a hard question does not say "I don't know"; it answers confidently and wrongly.
`test_a_confident_wrong_answer_escalates` is the load-bearing case: if a confabulation
satisfies the completion contract, escalation never fires on the queries it exists for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from unseen_university.devices.inference.domains import resolve_domain
from unseen_university.devices.inference.domains.base import BaseDomain
from unseen_university.devices.inference.domains.coding import CodingDomain
from unseen_university.devices.inference.domains.general import GeneralDomain


# ── stub rack ────────────────────────────────────────────────────────────────


@dataclass
class _StubResponse:
    text: str
    finish_reason: str = "stop"
    source_kind: str = "local"
    model: str = "stub-model"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0
    source_billing_type: str = "usage_based"


@dataclass
class _StubDevice:
    """Replies per escalation hop; records every request it saw."""

    replies: dict[int, object]  # hop -> _StubResponse | Exception
    seen: list = field(default_factory=list)

    def dispatch(self, request):
        self.seen.append(request)
        reply = self.replies[request.escalation_hop]
        if isinstance(reply, Exception):
            raise reply
        return reply


def _domain(replies, **kw) -> tuple[GeneralDomain, _StubDevice]:
    dev = _StubDevice(replies=replies)
    return GeneralDomain(inference_device=dev, **kw), dev


#: The answer check the eval injects: ground truth, not a self-report.
def _is_42(reply: str) -> bool:
    return reply.strip().endswith("42")


@pytest.fixture(autouse=True)
def _no_alarms(monkeypatch):
    """Capture system alarms instead of dropping files into the runtime dir."""
    import unseen_university.system_alarms as sa

    raised: list[dict] = []
    monkeypatch.setattr(sa, "raise_alarm", lambda **kw: raised.append(kw))
    return raised


# ── the default domain exists and is the default ─────────────────────────────


def test_general_domain_is_the_default_domain():
    """'The default is the general domain... everybody else descends from that.'"""
    d = resolve_domain("")
    assert isinstance(d, GeneralDomain)
    assert d.name == ""
    assert isinstance(d, BaseDomain) and not isinstance(d, CodingDomain)


def test_an_unknown_domain_descends_from_general_carrying_its_name():
    d = resolve_domain("prose")
    assert isinstance(d, GeneralDomain)
    assert d.name == "prose"  # name passed through, never collapsed


def test_coding_still_resolves_to_its_specialization():
    """Registering the general default must not shadow a registered specialization."""
    assert isinstance(resolve_domain("coding"), CodingDomain)


# ── one attempt is ONE dispatch: no loop, no tools ───────────────────────────


def test_an_attempt_is_a_single_toolless_dispatch():
    dom, dev = _domain({0: _StubResponse("ANSWER: 42")}, answer_check=_is_42)
    assert dom.ask("what is 17+25?") == "ANSWER: 42"
    assert len(dev.seen) == 1, "a chat attempt must be ONE dispatch, not an agentic loop"
    req = dev.seen[0]
    assert req.tools is None, "the general domain offers no tools"
    assert req.domain == ""
    assert req.escalation_hop == 0
    assert req.messages[-1]["content"] == "what is 17+25?"


def test_a_correct_answer_at_the_first_rung_does_not_escalate():
    dom, dev = _domain({0: _StubResponse("ANSWER: 42")}, answer_check=_is_42)
    assert dom.ask("q") == "ANSWER: 42"
    assert [r.escalation_hop for r in dev.seen] == [0], "no bump on a passing answer"


# ── THE test: a confident wrong answer must escalate ─────────────────────────


def test_a_confident_wrong_answer_escalates_and_the_next_rung_answers():
    """A weak model confabulates; the walk bumps ONE rung and the stronger rung answers.

    This is the whole prototype. If a confabulation satisfied the completion contract, the
    walk would return the wrong answer at hop 0 and escalation would be decorative.
    """
    dom, dev = _domain(
        {0: _StubResponse("ANSWER: 32"), 1: _StubResponse("ANSWER: 42")},
        answer_check=_is_42,
    )
    assert dom.ask("what is 17+25?") == "ANSWER: 42"
    assert [r.escalation_hop for r in dev.seen] == [0, 1], "exactly one capability bump"


def test_the_prior_attempt_is_handed_to_the_stronger_rung():
    dom, dev = _domain(
        {0: _StubResponse("ANSWER: 32"), 1: _StubResponse("ANSWER: 42")},
        answer_check=_is_42,
    )
    dom.ask("q")
    assert "32" in dev.seen[1].prior_attempt, "the next rung must see what the last one tried"


def test_wrong_at_every_rung_halts_with_the_capability_ceiling_alarm(_no_alarms):
    dom, dev = _domain(
        {0: _StubResponse("ANSWER: 32"), 1: _StubResponse("ANSWER: 33")},
        answer_check=_is_42,
    )
    assert dom.ask("q") is None, "a walk that never verified must not return an answer"
    assert [r.escalation_hop for r in dev.seen] == [0, 1]
    assert any("capability-ceiling" in a["signature"] for a in _no_alarms), (
        "topping out must raise the capability-ceiling alarm, not silently return prose"
    )


# ── availability is not capability: a down source must not spend up ──────────


def test_a_down_source_retries_the_same_rung_and_never_bumps():
    """'Hex-DOWN is not a branch.' An availability wall must not walk onto a pricier tier."""
    dom, dev = _domain(
        {0: _StubResponse("", finish_reason="error", source_kind="none")},
        answer_check=_is_42,
    )
    assert dom.ask("q") is None
    hops = [r.escalation_hop for r in dev.seen]
    assert set(hops) == {0}, f"availability must re-select at the SAME rung, saw hops={hops}"


def test_a_raising_dispatch_is_availability_not_capability():
    dom, dev = _domain({0: RuntimeError("connection refused")}, answer_check=_is_42)
    assert dom.ask("q") is None
    assert set(r.escalation_hop for r in dev.seen) == {0}, "a raise must never spend up a tier"


# ── the default completion contract is honest about what it cannot do ────────


def test_the_default_answer_check_cannot_detect_a_confabulation():
    """Without an injected check, ANY non-empty reply is DONE — the named deferred lever.

    This is not a bug to fix here; it is the open question (runtime escalation trigger for
    open chat with no ground truth). Pinned as a test so it can never become an unnoticed
    assumption: a future edit that makes the default *look* smarter must change this test.
    """
    dom, dev = _domain({0: _StubResponse("Paris is the capital of Sweden.")})
    assert dom.ask("q") == "Paris is the capital of Sweden."
    assert len(dev.seen) == 1, "the default check escalates on nothing"


def test_an_empty_reply_is_not_done_under_the_default_check():
    dom, dev = _domain({0: _StubResponse("   "), 1: _StubResponse("something")})
    assert dom.ask("q") == "something"
    assert [r.escalation_hop for r in dev.seen] == [0, 1]


# ── the general domain owns the default escalation policy, not the proxy ─────


def test_the_domain_not_the_proxy_owns_the_escalation_walk():
    """Amendment 1: the DEFAULT escalation policy lives on the general domain."""
    import unseen_university.devices.inference.device as device_mod

    src = __import__("pathlib").Path(device_mod.__file__).read_text()
    assert "escalation_hop=request.escalation_hop" in src, (
        "the proxy passes the hop through; it does not decide to bump"
    )
    assert hasattr(GeneralDomain, "run") and hasattr(GeneralDomain, "_classify")

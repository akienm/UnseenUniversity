"""
The escalation handoff must carry the CONCLUSION of a failed attempt, marked as failed.

T-escalation-handoff-transmits-the-confabulation. Measured on the live rack, one variable
isolated (same query, same model, same budget, temperature 0 — only the injected system
prompt differs):

    deepseek-r1:32b, no handoff                             -> PASS  'both apples and oranges'
    deepseek-r1:32b, the hop-1 handoff as shipped           -> FAIL  'Oranges and Apples'
    deepseek-r1:32b, conclusion-only + marked WRONG         -> PASS  'Both apples and oranges'

The stronger rung answers the query correctly on its own and is TALKED OUT OF IT by the
handoff. `prior_attempt = result.text[:400]` slices the OPENING of a reasoning model's <think>
scratchpad, cut mid-sentence, and `device.dispatch` injects it under '**What was tried:**' with
nothing saying it failed. Escalation was transmitting the confabulation upward.

Hermetic: stub device, no live InferenceDevice (whose HealthMonitor probes real providers, so a
proof built on one passes on the weather).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from unseen_university.devices.inference.domains.base import FAILED_MARKER
from unseen_university.devices.inference.domains.general import GeneralDomain

#: A reasoning model's reply: a long confident scratchpad, then a wrong conclusion. The first
#: 400 characters are ALL scratchpad — which is precisely what got handed to the next rung.
WEAK_RAMBLE = (
    "<think>\n"
    + ("The box labelled 'both' cannot contain both fruits since all labels are wrong. "
       "Drawing an apple means it contains only apples. The remaining boxes must then "
       "contain the other two types: one has only oranges, and the other has both. Since ") * 4
    + "\n</think>\n"
    "Therefore the box labelled 'oranges' contains only apples.\n"
    "ANSWER: apples"
)
STRONG_CORRECT = "<think>careful re-derivation</think>\nANSWER: both"

#: The scratchpad's opening — the substring that must NEVER reach the next rung.
RAMBLE_FRAGMENT = "cannot contain both fruits since all labels are wrong"


@dataclass
class _StubResponse:
    text: str
    finish_reason: str = "stop"
    source_kind: str = "local"
    model: str = "stub"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0
    source_billing_type: str = "usage_based"


@dataclass
class _StubDevice:
    replies: dict[int, _StubResponse]
    seen: list = field(default_factory=list)

    def dispatch(self, request):
        self.seen.append(request)
        return self.replies[request.escalation_hop]


def _answer_is_both(reply: str) -> bool:
    from unseen_university.devices.inference.domains.reply_text import extract_answer, normalize

    return normalize(extract_answer(reply)) == "both"


@pytest.fixture(autouse=True)
def _no_alarms(monkeypatch):
    import unseen_university.system_alarms as sa

    monkeypatch.setattr(sa, "raise_alarm", lambda **kw: None)


def _run() -> _StubDevice:
    dev = _StubDevice(replies={0: _StubResponse(WEAK_RAMBLE), 1: _StubResponse(STRONG_CORRECT)})
    dom = GeneralDomain(inference_device=dev, answer_check=_answer_is_both)
    assert dom.ask("which box holds both?") is not None, "the stub's hop-1 reply is correct"
    assert len(dev.seen) == 2, "expected exactly one capability bump"
    return dev


def test_the_handoff_carries_the_conclusion_not_the_scratchpad():
    """The next rung must see WHAT WAS CONCLUDED, never the scratchpad that produced it."""
    prior = _run().seen[1].prior_attempt
    assert "ANSWER: apples" in prior, (
        "the handoff dropped the failed attempt's conclusion — the next rung cannot know "
        "which answer was already ruled out"
    )


def test_the_handoff_does_not_leak_the_weak_models_reasoning():
    """THE test. A confident partial derivation is what the stronger model follows."""
    prior = _run().seen[1].prior_attempt
    assert RAMBLE_FRAGMENT not in prior, (
        "the handoff leaked the weak model's <think> scratchpad. Measured live: this makes "
        "deepseek-r1:32b abandon its own correct answer and adopt deepseek-r1:14b's wrong one."
    )
    assert "<think" not in prior.lower()


def test_the_handoff_states_that_the_prior_attempt_failed():
    """'What was tried:' reads as useful prior work. It was a FAILURE and must say so.

    Asserts an exact marker phrase, not a loose word-set. A word-set like
    ('failed', 'wrong', 'incorrect') is satisfied *by the ramble itself* — the weak model's
    scratchpad contains "all labels are wrong" — so the guard would pass on precisely the
    input it exists to reject.
    """
    prior = _run().seen[1].prior_attempt
    assert FAILED_MARKER in prior, f"nothing marks the prior attempt as failed: {prior!r}"


def test_hop_zero_carries_no_handoff():
    assert _run().seen[0].prior_attempt == ""


def test_a_short_non_reasoning_reply_survives_intact():
    """A model with no <think> block must still hand its answer forward."""
    dev = _StubDevice(replies={0: _StubResponse("ANSWER: apples"),
                               1: _StubResponse(STRONG_CORRECT)})
    GeneralDomain(inference_device=dev, answer_check=_answer_is_both).ask("q")
    assert "ANSWER: apples" in dev.seen[1].prior_attempt


def test_a_truncated_scratchpad_hands_over_nothing_rather_than_rambling():
    """An unclosed <think> is pure scratchpad: there is no conclusion to pass on."""
    dev = _StubDevice(replies={0: _StubResponse("<think>ramble ramble " + RAMBLE_FRAGMENT),
                               1: _StubResponse(STRONG_CORRECT)})
    GeneralDomain(inference_device=dev, answer_check=_answer_is_both).ask("q")
    assert RAMBLE_FRAGMENT not in dev.seen[1].prior_attempt

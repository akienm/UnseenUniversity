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

from unseen_university.devices.inference.domains.agentic_loop import LoopResult
from unseen_university.devices.inference.domains.base import FAILED_MARKER
from unseen_university.devices.inference.domains.general import GeneralDomain

#: The failed attempt's ARGUMENT, restated outside <think>. Measured: handing this to
#: deepseek-r1:32b makes it reproduce the weak model's wrong answer verbatim, even when the
#: handoff explicitly says "do not continue its reasoning". Only the final CLAIM may cross a
#: rung — never the argument for it.
WEAK_PROSE = "Therefore the box labelled oranges must contain only apples."

#: A reasoning model's reply. Two traps live here. (1) The first 400 characters are all
#: <think> scratchpad — which is what the old slice handed over. (2) deepseek-r1 RESTATES its
#: whole derivation *outside* the think block, so stripping the scratchpad is not enough: the
#: wrong argument is still there, in prose, sounding confident.
WEAK_RAMBLE = (
    "<think>\n"
    + ("The box labelled 'both' cannot contain both fruits since all labels are wrong. "
       "Drawing an apple means it contains only apples. Since ") * 6
    + "\n</think>\n"
    + WEAK_PROSE
    + "\nANSWER: apples-only"
)
STRONG_CORRECT = "<think>careful re-derivation</think>\nANSWER: both"

#: The scratchpad's opening — must never reach the next rung.
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


def test_the_handoff_carries_the_failed_claim():
    """The next rung must learn WHICH ANSWER was already ruled out."""
    prior = _run().seen[1].prior_attempt
    assert "apples-only" in prior, (
        "the handoff dropped the failed attempt's answer — the next rung cannot know which "
        "answer was already ruled out"
    )


def test_the_handoff_does_not_leak_the_weak_models_reasoning():
    """THE test. Only the final CLAIM crosses a rung — never the argument for it.

    Stripping <think> is NOT sufficient: deepseek-r1 restates its derivation in prose outside
    the block. Measured live — handed that prose, deepseek-r1:32b reproduced the weak model's
    wrong answer verbatim, even though the handoff said "do not continue its reasoning".
    Handed only the failed answer, it solved the query correctly.
    """
    prior = _run().seen[1].prior_attempt
    assert RAMBLE_FRAGMENT not in prior, "the handoff leaked the weak model's <think> scratchpad"
    assert WEAK_PROSE not in prior, (
        "the handoff leaked the weak model's ARGUMENT (restated outside <think>). The stronger "
        "model follows it and reproduces the wrong answer."
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
    dev = _StubDevice(replies={0: _StubResponse("ANSWER: apples-only"),
                               1: _StubResponse(STRONG_CORRECT)})
    GeneralDomain(inference_device=dev, answer_check=_answer_is_both).ask("q")
    assert "apples-only" in dev.seen[1].prior_attempt


def test_a_truncated_scratchpad_hands_over_nothing_rather_than_rambling():
    """An unclosed <think> is pure scratchpad: there is no conclusion to pass on."""
    dev = _StubDevice(replies={0: _StubResponse("<think>ramble ramble " + RAMBLE_FRAGMENT),
                               1: _StubResponse(STRONG_CORRECT)})
    GeneralDomain(inference_device=dev, answer_check=_answer_is_both).ask("q")
    assert RAMBLE_FRAGMENT not in dev.seen[1].prior_attempt


# ── the shared path: what CodingDomain currently emits ────────────────────────


def test_coding_domains_handoff_is_chat_shaped_and_wrong_for_coding():
    """PINS A KNOWN DEFECT. `_summarize_attempt` lives on BaseDomain — the SHARED escalation
    path — and its default assumes the attempt's output is a final CLAIM. A coding attempt's
    output is a diff, a test result, an error. `extract_answer` finds no `ANSWER:` line and
    falls back to the last non-empty line, so the handoff tells the next rung that a failing
    test's output was "the answer", that it is "known to be WRONG", and not to repeat it.

    That is nonsense for coding: the failing test output is the most USEFUL thing to pass on,
    not a claim to avoid. The rule still holds (only the claim crosses, never the argument) —
    but CodingDomain must decide what its claim IS, via its own `_summarize_attempt` override.

    Not a regression: the prior `text[:400]` was also wrong here (it handed over the opening of
    the attempt, scratchpad and all). This is a lateral move on an already-broken path, and DS
    is not running. The test exists so the shape is visible and the ticket has teeth.

    => T-escalation-handoff-transmits-the-confabulation (closing note) / CodingDomain override.
    """
    from unseen_university.devices.inference.domains.agentic_loop import LOOP_MAX_TURNS
    from unseen_university.devices.inference.domains.coding import CodingDomain

    result = LoopResult(
        LOOP_MAX_TURNS,
        text="I ran the tests.\n\n```diff\n- old\n+ new\n```\nTests still fail: AssertionError in test_foo",
    )
    handoff = CodingDomain()._summarize_attempt(result)

    # The scratchpad/derivation rule IS honoured — the diff body does not cross.
    assert "- old" not in handoff and "+ new" not in handoff

    # ...but the "claim" it extracted is a test failure, framed as a wrong answer. Documented,
    # not endorsed. When CodingDomain overrides _summarize_attempt, this assertion must change.
    assert "Tests still fail: AssertionError in test_foo" in handoff
    assert FAILED_MARKER in handoff
    assert "do not repeat that answer" in handoff, (
        "coding's handoff currently instructs the next rung not to repeat a TEST FAILURE, as "
        "though it were a wrong answer — the chat-shaped default applied to a coding attempt"
    )

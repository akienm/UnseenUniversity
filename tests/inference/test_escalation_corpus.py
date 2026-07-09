"""
The escalation eval corpus is an INSTRUMENT. These tests are its calibration.

T-inference-escalation-eval-corpus. Fully hermetic: pure data, pure functions, no device,
no provider, no network. A proof that builds a live InferenceDevice() passes on the weather
(its HealthMonitor probes real providers) — that is how a defective proof went green on
2026-07-08.

The load-bearing test here is `test_every_verifier_rejects_its_confabulation`. A verifier
that only rejects an empty reply is useless: a weak model asked a hard question does not
return nothing, it returns a confident wrong answer. If the verifier accepts that, the
escalation trigger never fires on exactly the queries the corpus exists to catch.
"""

from __future__ import annotations

import pytest

from unseen_university.devices.inference.domains.escalation_corpus import (
    ANSWER_INSTRUCTION,
    BANDS,
    CORPUS,
    EvalQuery,
    band_rank,
    by_band,
    extract_answer,
    normalize,
    strip_reasoning,
)


def test_every_band_is_populated():
    """A band with no queries measures nothing — the frontier would be invisible there."""
    for band in BANDS:
        assert by_band(band), f"band {band!r} is empty — the corpus cannot locate a frontier in it"


def test_bands_are_ranked_easy_to_hard():
    assert [band_rank(b) for b in BANDS] == list(range(len(BANDS)))
    assert band_rank("no-such-band") >= len(BANDS), "unknown band must not rank as easy"


def test_every_query_declares_a_real_band():
    for q in CORPUS:
        assert q.band in BANDS, f"{q.id} declares unknown band {q.band!r}"


def test_query_ids_are_unique():
    ids = [q.id for q in CORPUS]
    assert len(ids) == len(set(ids)), "duplicate query id — the capability matrix would collide"


def test_every_verifier_accepts_its_ground_truth():
    for q in CORPUS:
        assert q.verify(f"Reasoning here.\nANSWER: {q.answer}"), f"{q.id} rejects its own answer"


def test_every_verifier_accepts_the_phrasings_a_correct_model_actually_emits():
    """The OTHER side of the classifier. Rejecting a right answer is as fatal as accepting a wrong one.

    T-escalation-corpus-false-negatives. The first live matrix reported deepseek-r1:32b as LESS
    capable than deepseek-r1:14b. It was not: it answered 'ANSWER: 100 litres' where ground truth
    was '100', and 'ANSWER: both apples and oranges' where ground truth was 'both'. Exact string
    equality scored both as wrong. An instrument that under-reports capability pushes every model
    onto a lower rung — and the tier ladder is about to be rebuilt on exactly this measurement.

    The prior proof (rejects-its-confabulation) constrained only FALSE POSITIVES. A one-sided
    proof on a classifier proves one side.
    """
    assert CORPUS, "empty corpus — this test would pass vacuously"
    for q in CORPUS:
        assert q.accepts, f"{q.id} declares no accepted phrasings"
        for phrasing in q.accepts:
            assert q.verify(f"Working through it.\nANSWER: {phrasing}"), (
                f"{q.id} REJECTS the correct answer {phrasing!r} — a false negative makes a "
                f"capable model look like it belongs on a lower rung"
            )


def test_every_verifier_rejects_its_confabulation():
    """THE test. Ground truth must reject a confident wrong answer, not just an empty one.

    A weak model does not say 'I don't know'; it confabulates. If a verifier passes the
    plausible wrong answer, the hard queries never trigger escalation and the whole eval
    goes green while proving nothing (the 2026-07-08 failure, exactly).

    The non-empty guard is not ceremony: `for q in ()` passes this test vacuously, which is
    the same shape of hollow green it is written to prevent.
    """
    assert CORPUS, "empty corpus — this test would pass vacuously"
    for q in CORPUS:
        assert q.answer != q.confabulation, f"{q.id}: confabulation must differ from the answer"
        reply = f"Let me work through this carefully.\nANSWER: {q.confabulation}"
        assert not q.verify(reply), (
            f"{q.id} ACCEPTS its confabulation {q.confabulation!r} — this verifier cannot "
            f"detect the failure mode escalation exists for"
        )


def test_verifier_rejects_an_empty_and_a_refusing_reply():
    for q in CORPUS:
        assert not q.verify("")
        assert not q.verify("I'm not sure I can answer that.")


def test_verifier_rejects_a_negated_answer():
    """'The answer is not 42' must not verify as 42 — a substring check would pass it."""
    q = CORPUS[0]
    assert not q.verify(f"ANSWER: the answer is definitely not {q.answer}")


def test_strip_reasoning_removes_think_blocks():
    assert strip_reasoning("<think>ramble</think>ANSWER: 42").strip() == "ANSWER: 42"
    # multiline + multiple blocks
    assert "ramble" not in strip_reasoning("<think>\nramble\n</think>\nANSWER: 42")
    # an UNCLOSED think block is a truncated reply: everything after it is reasoning, not answer
    assert strip_reasoning("<think>ramble ANSWER: 42").strip() == ""


def test_strip_reasoning_leaves_plain_text_alone():
    assert strip_reasoning("ANSWER: 42") == "ANSWER: 42"


def test_normalize_canonicalizes_money_commas_and_case():
    assert normalize("$1,024.") == normalize("1024")
    assert normalize("  Carl  ") == normalize("carl")
    assert normalize("14:15") == "14:15"


def test_extract_answer_prefers_the_tagged_line():
    assert normalize(extract_answer("blah\nANSWER: 42\n")) == "42"
    # last tag wins — a model that restates its answer must not be read from the first draft
    assert normalize(extract_answer("ANSWER: 7\nwait, no.\nANSWER: 42")) == "42"


def test_extract_answer_falls_back_to_the_last_line():
    """A correct answer without the tag is still correct — do not confound with instruction-following."""
    assert normalize(extract_answer("The calculation gives\n42")) == "42"


def test_answer_instruction_is_attached_to_prompts_by_the_runner_not_baked_in():
    """The instruction is a separate constant so a caller can measure tagged vs untagged."""
    assert "ANSWER:" in ANSWER_INSTRUCTION
    for q in CORPUS:
        assert ANSWER_INSTRUCTION not in q.prompt, f"{q.id} bakes in the answer instruction"


def test_corpus_entries_are_frozen():
    with pytest.raises(Exception):
        CORPUS[0].answer = "tampered"  # type: ignore[misc]


def test_eval_query_is_the_corpus_element_type():
    assert CORPUS and all(isinstance(q, EvalQuery) for q in CORPUS)

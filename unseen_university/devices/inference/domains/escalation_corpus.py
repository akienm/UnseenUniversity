"""
escalation_corpus.py — graduated queries with ground-truth verifiers.

T-inference-escalation-eval-corpus. This module is the LEVER that turns a model's
`difficulty_bucket` from a self-assigned CLAIM into a MEASUREMENT.

Why it has to exist
-------------------
The cost-optimizing selector sorts by cost_class FIRST, and `difficulty_meets` is a
`>=` filter. So a cheap model that *claims* the top difficulty bucket wins every bucket
beneath it too. The claim is hand-typed and nothing checks it — the selector actively
REWARDS overclaiming (T-inference-cost-first-sort-strands-cloud-fleet). A corpus of
queries with deterministic, checkable answers is the check: run every query against
every model, and the pass-frontier IS that model's real capability rung.

Why the answers must be ground truth, not a self-report
-------------------------------------------------------
A weak model asked a hard question does not say "I don't know" — it emits a confident
wrong answer. On 2026-07-08 `deepseek-r1:14b` returned `{"status":"done","result":"wrote
smoke file"}` and wrote nothing; the proof went green because it believed the model. An
escalation trigger that reads a self-reported confidence or refusal token will therefore
NEVER fire on exactly the queries it exists for. Every verifier here must reject a
plausible confabulation, not merely an empty string — hence `EvalQuery.confabulation`.

Why bands are defined by STRUCTURE, not by "which model passes"
---------------------------------------------------------------
Naming a band "the one deepseek-r1:14b fails" would bake today's guess into the
instrument and measure nothing. Bands are defined by the intrinsic shape of the
reasoning: how many dependent steps, whether a distractor must be discarded, whether
constraints must be jointly satisfied. Which model clears which band is the OUTPUT.

There is no LLM judge anywhere in this module. A judge is itself a model whose rung is
unmeasured; using one would make the instrument depend on the thing it measures.
"""

from __future__ import annotations

from dataclasses import dataclass

from unseen_university.devices.inference.domains.reply_text import (
    extract_answer,
    normalize,
    strip_reasoning,
)

# ── Bands, easy → hard. The index IS the rank. ────────────────────────────────
#: A band names the SHAPE of the reasoning a query demands, never a model.
BANDS: tuple[str, ...] = (
    "b1_single_step",         # one operation; no distractor
    "b2_multi_step",          # 2-4 dependent steps, one irrelevant fact to discard
    "b3_constraint",          # jointly satisfy constraints to a unique answer
    "b4_multi_hop_constraint",  # search/deduce across interacting constraints
    "b5_frontier",              # exhaustive search or case-analysis over a space too big to eyeball
)

#: Appended to every query BY THE RUNNER so the answer lands somewhere a verifier can find
#: it. Kept out of `EvalQuery.prompt` so a runner can measure tagged vs untagged separately —
#: a model that solves a query but ignores the format is failing instruction-following, not
#: reasoning, and conflating the two would corrupt the capability matrix.
ANSWER_INSTRUCTION = "End your reply with a final line of exactly: ANSWER: <your answer>"

#: Re-exported so importers of the corpus keep one import site for reply parsing.
__all__ = [
    "ANSWER_INSTRUCTION", "BANDS", "CORPUS", "EvalQuery",
    "band_rank", "by_band", "extract_answer", "normalize", "strip_reasoning",
]


def band_rank(band: str) -> int:
    """Rank of a band (lower = easier). Unknown band ranks past the hardest known one.

    Unknown ranks HARD, not easy: an unlabelled query must never be mistaken for a
    trivial one when locating a model's frontier.
    """
    try:
        return BANDS.index(band)
    except ValueError:
        return len(BANDS)


@dataclass(frozen=True)
class EvalQuery:
    """One graduated query with a deterministic ground-truth verifier.

    `confabulation` is the plausible WRONG answer a model actually reaches when it fails
    this query — the distractor it forgot to discard, the constraint it dropped, the other
    person it answered about. It is not decoration: the corpus's own test asserts every
    verifier REJECTS it. A verifier that only rejects an empty reply cannot detect the
    failure mode escalation exists for.
    """

    id: str
    band: str
    prompt: str
    answer: str
    confabulation: str
    #: Correct phrasings a model ACTUALLY emits — the answer carrying its unit ('100 litres'),
    #: spelled out ('both apples and oranges'), or with its noun ('48 books'). Rejecting these
    #: is a false negative, and a false negative makes a capable model look like it belongs on a
    #: lower rung. `answer` is always accepted and need not be repeated here.
    #: (T-escalation-corpus-false-negatives.)
    accepts: tuple[str, ...] = ()

    @property
    def accepted_forms(self) -> tuple[str, ...]:
        """Ground truth plus every phrasing that means the same thing."""
        return (self.answer, *self.accepts)

    def verify(self, reply: str) -> bool:
        """True iff `reply`'s extracted answer equals one of the accepted forms.

        Equality on the normalized form, never a substring test: 'the answer is not 42'
        contains '42' and must not verify as 42. Equality against a SET of forms rather than
        one string, because a correct model writes '100 litres', not '100'.
        """
        got = normalize(extract_answer(reply))
        return any(got == normalize(form) for form in self.accepted_forms)


CORPUS: tuple[EvalQuery, ...] = (
    # ── b1: one operation, nothing to discard ────────────────────────────────
    EvalQuery(
        id="b1-sum", band="b1_single_step",
        prompt="What is 17 + 25?",
        answer="42", confabulation="32",
        accepts=("42", "17 + 25 = 42"),
    ),
    EvalQuery(
        id="b1-shelves", band="b1_single_step",
        prompt="A shelf holds 8 books. How many books do 6 identical shelves hold?",
        answer="48", confabulation="14",  # added instead of multiplied
        accepts=("48 books", "48 book"),
    ),
    EvalQuery(
        id="b1-letter", band="b1_single_step",
        prompt="What is the 5th letter of the English alphabet?",
        answer="e", confabulation="f",  # off by one
        accepts=("e", "the letter e"),
    ),
    # ── b2: dependent steps + one irrelevant fact that must be discarded ─────
    EvalQuery(
        id="b2-muffins", band="b2_multi_step",
        prompt=(
            "A baker made 48 muffins. He sold three quarters of them, then baked 12 more. "
            "A separate tray holds 20 cupcakes. How many muffins does he have now?"
        ),
        answer="24", confabulation="44",  # folded the cupcake distractor in
        accepts=("24 muffins", "24 muffin"),
    ),
    EvalQuery(
        id="b2-ages", band="b2_multi_step",
        prompt=(
            "Sarah is exactly twice as old as Tom. Sarah's cat is 3 years old. In 5 years, "
            "Sarah's age plus Tom's age will be 40. How old is Tom now?"
        ),
        answer="10", confabulation="20",  # answered about Sarah, the wrong person
        accepts=("10 years old", "10 years", "tom is 10"),
    ),
    EvalQuery(
        id="b2-tank", band="b2_multi_step",
        prompt=(
            "A tank holds 200 litres when full and stands 1.5 m tall. It is currently 40% "
            "full. You add 30 litres, then remove 10 litres. How many litres are in it now?"
        ),
        answer="100", confabulation="60",  # read 40% as 40 litres
        # deepseek-r1:32b emits exactly "100 litres" — scored wrong before this existed.
        accepts=("100 litres", "100 liters", "100 l"),
    ),
    # ── b3: constraints that must be satisfied jointly, unique answer ────────
    EvalQuery(
        id="b3-pets", band="b3_constraint",
        prompt=(
            "Ann, Ben and Cy each own exactly one pet, and the pets are a cat, a dog and a "
            "fish — one each. Ann does not own the cat. Ben owns neither the cat nor the "
            "fish. Who owns the cat?"
        ),
        answer="Cy", confabulation="Ann",
        accepts=("cy", "cy owns the cat"),
    ),
    EvalQuery(
        id="b3-digits", band="b3_constraint",
        prompt=(
            "A two-digit number has digits that sum to 11, and its tens digit is 3 more "
            "than its units digit. What is the number?"
        ),
        answer="74", confabulation="47",  # digits swapped
        accepts=("74",),
    ),
    EvalQuery(
        id="b3-race", band="b3_constraint",
        prompt=(
            "In a four-person race, Alice finished before Bob. Carl finished after Bob. "
            "Dana finished before Alice. Who finished last?"
        ),
        answer="Carl", confabulation="Bob",
        accepts=("carl", "carl finished last"),
    ),
    # ── b4: search or deduce across interacting constraints ──────────────────
    EvalQuery(
        id="b4-pens", band="b4_multi_hop_constraint",
        prompt=(
            "A shop sells pens for $3 each and notebooks for $7 each. Maya spent exactly "
            "$61, bought at least one of each, and bought more notebooks than pens. How "
            "many pens did she buy?"
        ),
        # 3p + 7n = 61 has three positive solutions: (p=4,n=7), (p=11,n=4), (p=18,n=1).
        # Only p=4 satisfies n > p.
        answer="4", confabulation="11",  # a real solution that violates n > p
        accepts=("4 pens", "4 pen"),
    ),
    EvalQuery(
        id="b4-boxes", band="b4_multi_hop_constraint",
        prompt=(
            "Three boxes contain fruit: one holds only apples, one holds only oranges, and "
            "one holds both. All three boxes are labelled, and every label is wrong. You "
            "draw one fruit from the box labelled 'both' and it is an apple. What does the "
            "box labelled 'oranges' actually contain?"
        ),
        # 'both' is mislabelled → pure; the drawn apple makes it apples-only. 'oranges' is
        # mislabelled → not oranges, and apples-only is taken → it holds both.
        answer="both", confabulation="apples",
        # deepseek-r1:32b emits exactly "both apples and oranges" — correct, scored wrong before.
        accepts=("both apples and oranges", "apples and oranges", "both fruits"),
    ),
    EvalQuery(
        id="b4-schedule", band="b4_multi_hop_constraint",
        prompt=(
            "A meeting starts at 09:40 and runs for 150 minutes. It is followed by a "
            "25-minute break, and then a session lasting two thirds as long as the meeting. "
            "At what time does the session end? Give a 24-hour time as HH:MM."
        ),
        # 09:40 +150m = 12:10; +25m = 12:35; session = 100m → 14:15.
        answer="14:15", confabulation="13:50",  # dropped the break
        accepts=("1415", "14:15 (2:15 pm)"),
    ),
    # ── b5: exhaustive search or case-analysis over a space too big to eyeball ──
    # Added because the corpus SATURATED: deepseek-r1:32b cleared b1-b4 completely, so no
    # measurement could place a cloud model above the top of the local box. Every ground truth
    # below was brute-forced before it entered the corpus — a wrong answer here would corrupt
    # every capability rank derived from it.
    EvalQuery(
        id="b5-knights", band="b5_frontier",
        prompt=(
            "On an island, knights always tell the truth and knaves always lie. Every "
            "inhabitant is exactly one of the two. You meet three inhabitants: A, B and C.\n"
            "A says: 'B is a knave.'\n"
            "B says: 'A and C are the same type as each other.'\n"
            "C says: 'I am a knight.'\n"
            "How many of the three are knaves?"
        ),
        # Two assignments are consistent — (A knave, B knight, C knave) and (A knight, B knave,
        # C knave) — and BOTH contain exactly 2 knaves. The count is unique even though the
        # assignment is not, which is what makes the query checkable and the reasoning hard.
        answer="2", confabulation="1",
        accepts=("2 knaves", "two"),
    ),
    EvalQuery(
        id="b5-modular", band="b5_frontier",
        prompt=(
            "What is the smallest positive integer that is divisible by 7, and that leaves a "
            "remainder of exactly 1 when divided by each of 2, 3, 4, 5 and 6?"
        ),
        answer="301", confabulation="61",  # 60k+1 without checking divisibility by 7
        accepts=("301",),
    ),
    EvalQuery(
        id="b5-frobenius", band="b5_frontier",
        prompt=(
            "A shop sells items only in packs of 6, 9 and 20 units. Any whole number of units "
            "may be bought using any combination of packs, including none of a given pack. "
            "What is the LARGEST number of units that cannot be bought exactly?"
        ),
        answer="43", confabulation="41",
        accepts=("43 units",),
    ),
    EvalQuery(
        id="b5-digitcount", band="b5_frontier",
        prompt=(
            "How many four-digit positive integers have digits that sum to exactly 9, where "
            "none of the four digits is zero?"
        ),
        answer="56", confabulation="84",  # stars-and-bars without the no-zero constraint
        accepts=("56",),
    ),
)


def by_band(band: str) -> tuple[EvalQuery, ...]:
    """Every query in `band`, in corpus order."""
    return tuple(q for q in CORPUS if q.band == band)

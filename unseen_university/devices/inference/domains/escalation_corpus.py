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
plausible confabulation, not merely an empty string.

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

import re
from dataclasses import dataclass

# ── Bands, easy → hard. The index IS the rank. ────────────────────────────────
#: A band names the SHAPE of the reasoning a query demands, never a model.
BANDS: tuple[str, ...] = (
    "b1_single_step",         # one operation; no distractor
    "b2_multi_step",          # 2-4 dependent steps, one irrelevant fact to discard
    "b3_constraint",          # jointly satisfy constraints to a unique answer
    "b4_multi_hop_constraint",  # search/deduce across interacting constraints
)

#: Appended to every query so the answer lands somewhere a verifier can find it.
ANSWER_INSTRUCTION = "End your reply with a final line of exactly: ANSWER: <your answer>"


def band_rank(band: str) -> int:
    """Rank of a band (lower = easier). Unknown band ranks past the hardest known one."""
    raise NotImplementedError


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> reasoning blocks that reasoning models emit around answers."""
    raise NotImplementedError


def normalize(text: str) -> str:
    """Canonical form for answer comparison: casefold, strip money/commas/punctuation."""
    raise NotImplementedError


def extract_answer(text: str) -> str:
    """Pull the model's final answer out of a raw reply."""
    raise NotImplementedError


@dataclass(frozen=True)
class EvalQuery:
    """One graduated query with a deterministic ground-truth verifier.

    `confabulation` is the plausible WRONG answer a model actually reaches when it fails
    this query — the distractor it forgot to discard, the constraint it dropped. It is not
    decoration: the corpus's own test asserts every verifier REJECTS it. A verifier that
    only rejects an empty reply cannot detect the failure mode escalation exists for.
    """

    id: str
    band: str
    prompt: str
    answer: str
    confabulation: str

    def verify(self, reply: str) -> bool:
        """True iff `reply`'s extracted answer matches ground truth."""
        raise NotImplementedError


CORPUS: tuple[EvalQuery, ...] = ()


def by_band(band: str) -> tuple[EvalQuery, ...]:
    """Every query in `band`, in corpus order."""
    raise NotImplementedError

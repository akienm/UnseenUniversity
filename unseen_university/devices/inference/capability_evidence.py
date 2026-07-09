"""
capability_evidence.py — how we KNOW what a model can do, and under what conditions.

T-capability-measured-at-a-budget-ceiling.

A capability label is worthless without the conditions it was measured under. `difficulty_capable`
used to be a bare string a human typed, and the cost-optimizing selector REWARDS overclaiming: it
sorts by `cost_class` first and `difficulty_meets` is a `>=` filter, so a cheap model that claims
the top bucket captures every bucket beneath it too. Nothing checked the claim.

Adding the word "measured" did not fix that on its own, because a measurement without its
CONDITIONS is a wish with a number attached. Both of these are real, and both happened here:

  - `gemini-2.5-flash` was measured as LESS CAPABLE than a local 32b. Its replies had been cut
    off mid-derivation: the instrument did not recognise its provider's spelling of "out of
    budget" (`max_tokens`, where OpenAI/Ollama say `length`), so a truncation was scored as a
    wrong answer.
  - `deepseek-r1:14b` passed a frontier query at 8192 tokens that `deepseek-r1:32b` "failed" at
    4096. Two measurements at different budgets are not a comparison. No ordering was licensed.

So evidence is a VALUE OBJECT, not a string. It names the record, the ceiling budget the
capability pass ran at, and how many samples backed each cell. `tests/inference/
test_capability_evidence.py` resolves `record` against the memory store and reads the numbers
back — which is what makes this un-fakeable. A hand-typed `ceiling_tokens=32768` with no matching
note, or a note in which that model truncated, fails the guard. The registry cannot claim more
than the instrument measured.

Pure data: no I/O, no note resolution, no filesystem. The dev-process store (`devlab/runtime/
memory/`) is read by the TEST, never by the runtime package.
"""

from __future__ import annotations

from dataclasses import dataclass

#: A human typed the label. Honest, and explicitly not a measurement (CP1).
DECLARED_KIND = "declared"
#: The label came from running the escalation corpus and recording where the pass-rate collapsed.
MEASURED_KIND = "measured"

#: A capability pass backed by a single sample at temperature 0 is one sample, not a property.
#: The guard requires more, because the whole point of repeating a cell is to catch the boundary
#: flake that a single run reports as a clean verdict.
MIN_SAMPLES = 2


@dataclass(frozen=True)
class CapabilityEvidence:
    """Why we believe a `ModelSpec.difficulty_capable`, and under exactly what conditions.

    `record` names a memory-store note namespace (not a file — the note filename carries a
    timestamp). The guard globs for it and cross-checks that the note's `conditions` still match
    what the registry claims, so a later sweep at a different ceiling cannot silently leave a
    stale claim standing behind it.

    `ceiling_tokens` is the NON-BINDING budget the capability pass ran at. It is the condition
    that makes a wrong answer mean "cannot" rather than "ran out of room". `samples` is how many
    times each cell was run; a cell counts as a pass only if every sample passed.
    """

    kind: str = DECLARED_KIND
    record: str = ""
    ceiling_tokens: int = 0
    samples: int = 0

    @property
    def is_measured(self) -> bool:
        return self.kind == MEASURED_KIND

    def __str__(self) -> str:
        if not self.is_measured:
            return self.kind
        return f"{self.kind}:{self.record}@{self.ceiling_tokens}tok×{self.samples}"


#: The default. A model says nothing about itself until something measures it.
DECLARED = CapabilityEvidence()


def measured(record: str, *, ceiling_tokens: int, samples: int) -> CapabilityEvidence:
    """Build measured evidence. Every argument is a condition of the result — none is optional."""
    return CapabilityEvidence(
        kind=MEASURED_KIND, record=record, ceiling_tokens=ceiling_tokens, samples=samples
    )

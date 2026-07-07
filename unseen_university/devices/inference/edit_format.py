"""
edit_format.py — per-(model, format) edit-dialect selection (T-aider-port-editformat-conformance).

aider's hardest-won lesson: a small/quantized model that can't follow a strict edit format is not
fixed by 'try harder' — it's fixed by a SIMPLER contract (whole-file instead of SEARCH/REPLACE).
This extends our tier-not-model contract to the edit DIALECT: which format a model gets is a warm
lookup against conformance rates computed OFFLINE from corpus replay — zero inference, exactly like
tier routing.

Two moving parts, both deterministic:
  - ``compute_conformance`` folds corpus editor records into a per-(model, format) success rate.
    This is the OFFLINE step (replay → registry); it never calls a model.
  - ``select_edit_format`` is the WARM lookup: given a model's conformance map it returns the
    best format, defaulting to ``block`` — so an EMPTY registry (the state today: format was only
    just stamped onto records, and both editors are new) selects block, and the runtime ladder
    (block → whole-file fallback) does the real work until real data accrues.

⛔ NO SQLITE — conformance is plain data on ModelSpec.edit_format_conformance ({format: rate}).
"""

from __future__ import annotations

from collections import defaultdict

BLOCK = "block"
WHOLEFILE = "wholefile"


def compute_conformance(records) -> dict:
    """Fold editor corpus records into ``{(model, format): success_rate}`` (the offline replay step).

    A record needs ``model`` and ``format``; success = it produced ≥1 applied edit (``applied`` > 0),
    i.e. the model actually followed the dialect. Records missing model/format are ignored (they
    predate format stamping — which is exactly why stamping now is the enabler). Pure; no inference.
    """
    tally: dict = defaultdict(lambda: [0, 0])  # (model, format) -> [successes, total]
    for r in records:
        model = r.get("model")
        fmt = r.get("format")
        if not model or not fmt:
            continue
        tally[(model, fmt)][1] += 1
        if (r.get("applied") or 0) > 0:
            tally[(model, fmt)][0] += 1
    return {key: succ / total for key, (succ, total) in tally.items() if total > 0}


def conformance_for_model(conformance: dict, model: str) -> dict:
    """Slice a ``{(model, format): rate}`` map down to ``{format: rate}`` for one model."""
    return {fmt: rate for (m, fmt), rate in conformance.items() if m == model}


def select_edit_format(model_conformance: dict, default: str = BLOCK) -> str:
    """Warm lookup: the best format for a model given its ``{format: rate}`` map (no inference).

    Empty map → ``default`` (block). On a tie the default wins, so we only move OFF block when a
    format is measurably better — conservative, matching aider's 'simpler only when needed' posture.
    """
    if not model_conformance:
        return default
    # Sort by (rate, is-default) descending → highest rate, default breaks ties.
    best = max(model_conformance, key=lambda f: (model_conformance[f], f == default))
    return best

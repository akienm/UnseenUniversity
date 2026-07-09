#!/usr/bin/env python3
"""
escalation_matrix.py — sweep the escalation corpus across models AND token budgets.

T-inference-escalation-eval-corpus (the live half), extended by
T-capability-measured-at-a-budget-ceiling. This is a SMOKE RUN that produces DATA. It is not a
test and it asserts nothing: it reaches real providers, so its outcome depends on whether Hex is
up and whether a subscription is live. Asserting on that is how a proof goes green on the
weather (2026-07-08).

The two axes — and why conflating them fabricates a capability ladder
--------------------------------------------------------------------
CAPABILITY: can the model reach the right answer AT ALL? Measured at a `--ceiling` budget so
generous it cannot bind. If the budget binds, a wrong answer is ambiguous between "cannot" and
"ran out of room", and a truncated reply gets scored as a wrong one. That is not hypothetical:
`gemini-2.5-flash` was first measured as LESS CAPABLE THAN A LOCAL 32b, purely from replies cut
off mid-derivation (its provider spells the finish reason `max_tokens`, not `length`). And
`deepseek-r1:14b` passed a frontier query at 8192 tokens that `deepseek-r1:32b` "failed" at 4096
— two measurements at different budgets are not a comparison, and licensed no conclusion.

BUDGET: how many tokens does the model NEED? A cost property — the one routing actually spends
(`InferenceRequest.max_tokens`). Found by walking the budget DOWN from the ceiling until the
model stops passing. The last budget that still passes is that cell's TOKEN FLOOR.

A verdict is only licensed at the budget it was measured at. Every number this script emits
carries its conditions (see `--emit-note`).

Sampling
--------
`--samples` runs each cell more than once. The point is to DETECT INSTABILITY, not to average it
away: a cell is a clean PASS only if EVERY sample passed and NONE truncated. Mixed pass/fail is
recorded as UNSTABLE — a real result, and never rounded up to a pass by majority vote. One
sample at temperature 0 is one sample, not a property.

Cost safety (CP6)
-----------------
Models are grouped by their source's cost_class and the expensive ones are OFF by default:
  owned_local   — on by default (Hex; free at the margin)
  subscription  — --include-subscription (Ollama Pro; already sunk, no per-token charge)
  token_direct  — --include-paid (Anthropic; REAL DOLLARS PER QUERY)
free_throttled sits with subscription. Nothing paid runs unless you ask for it by name.

Usage
-----
  # capability pass: full corpus at a non-binding ceiling (the load-bearing measurement)
  python3 devlab/claudecode/escalation_matrix.py --phase capability --ceiling 32768 --samples 2

  # budget walk: for every cell that passed at the ceiling, find its token floor
  python3 devlab/claudecode/escalation_matrix.py --phase both --walk-bands b5_frontier

  # both, and write the result to the memory store as machine-readable evidence
  python3 devlab/claudecode/escalation_matrix.py --phase both --include-subscription --emit-note
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from unseen_university._uu_root import uu_root
from unseen_university.devices.inference.connections import default_connections
from unseen_university.devices.inference.domains.escalation_corpus import (
    ANSWER_INSTRUCTION,
    BANDS,
    CORPUS,
)
from unseen_university.devices.inference.models_registry import default_registry as default_models
from unseen_university.devices.inference.routing_buckets import cost_class_rank
from unseen_university.devices.inference.sources import default_registry as default_sources

#: The namespace the capability note is emitted under. `ModelSpec.capability_evidence.record`
#: points here, and tests/inference/test_capability_evidence.py resolves the pointer and reads
#: the numbers back — so a registry claim cannot be hand-typed without a matching measurement.
NOTE_NAMESPACE = "capability-ceiling-sweep"

#: cost_class -> the flag that must be passed before a model on that source is queried.
_GATE = {
    "graph_tree": None,
    "owned_local": None,
    "free_throttled": "include_subscription",
    "subscription": "include_subscription",
    "token_direct": "include_paid",
}

#: TRUNC is NOT a wrong answer. A reasoning model spends its budget inside <think>…</think>;
#: if it runs out before emitting the answer, `extract_answer` sees a truncated scratchpad and
#: returns "". Scoring that as `fail` would fabricate a capability frontier out of a token
#: budget — the same shape as reading a signal without checking what produced it.
#: UNSTABLE is likewise not a pass: the samples disagreed, so the cell has no verdict.
_PASS, _FAIL, _ERR, _TRUNC, _UNSTABLE = "PASS", "fail", "ERR", "TRUNC", "UNSTABLE"

_MARK = {_PASS: "✓", _FAIL: "✗", _ERR: "!", _TRUNC: "…", _UNSTABLE: "~"}

#: Budgets the walk steps DOWN through, ceiling-first. The floor is the smallest budget that
#: still yields a clean PASS; the walk stops at the first budget that does not.
DEFAULT_WALK_LADDER = (16384, 8192, 4096, 2048, 1024)


def _cheapest_source(model_id, connections, sources):
    """The source this model would be served from — cheapest connection, as the selector would."""
    conns = connections.by_model(model_id)
    best, best_rank = None, None
    for c in conns:
        src = sources.get(c.source_name)
        if src is None:
            continue
        rank = cost_class_rank(getattr(src, "cost_class", "token_direct"))
        if best_rank is None or rank < best_rank:
            best, best_rank = src, rank
    return best


#: Every spelling a provider uses for "I ran out of output budget". OpenAI/Ollama say "length";
#: Gemini says "max_tokens". Missing one silently scores a truncated reply as a WRONG ANSWER —
#: which is how gemini-2.5-flash was first measured as less capable than a local 32b, from
#: replies that were cut off mid-derivation.
_TRUNCATION_FINISH_REASONS = {"length", "max_tokens", "maxtokens", "max_output_tokens"}


def _unclosed_think(text: str) -> bool:
    """An opened <think> with no closing tag: the reply was cut off inside the scratchpad."""
    low = (text or "").lower()
    return "<think" in low and "</think" not in low


def _truncated(finish_reason: str, text: str) -> bool:
    """True if the reply ran out of budget before the model finished. NOT a wrong answer."""
    return (finish_reason or "").strip().lower() in _TRUNCATION_FINISH_REASONS or _unclosed_think(text)


def _ask(device, model_id, query, timeout, max_tokens):
    """Pin `model_id` and ask one corpus query at one budget. Returns (verdict, reply_text)."""
    from unseen_university.devices.inference.shim import InferenceRequest

    req = InferenceRequest(
        messages=[{"role": "user", "content": f"{query.prompt}\n\n{ANSWER_INSTRUCTION}"}],
        model=model_id,
        # A pin is what makes this a MEASUREMENT of the model rather than of the router.
        pin_reason="model_competition",
        max_tokens=max_tokens,
        temperature=0.0,
        timeout=timeout,
        ticket_id=f"eval:{query.id}",
    )
    try:
        resp = device.dispatch(req)
    except Exception as exc:  # a down source is not a wrong answer — never score it as one
        return _ERR, f"{type(exc).__name__}: {exc}"
    if resp.finish_reason == "error" or resp.source_kind == "none":
        return _ERR, resp.text or "(no live source)"
    text = resp.text or ""
    if query.verify(text):
        return _PASS, text
    # Ran out of budget mid-thought → no answer was ever emitted. Not a wrong answer.
    if _truncated(resp.finish_reason, text):
        return _TRUNC, text
    return _FAIL, text


def _aggregate(samples: list[str]) -> str:
    """Collapse repeated samples of ONE cell into a single verdict.

    A clean PASS requires unanimity AND zero truncation. Anything that ran out of budget or
    hit a source error leaves the cell without a verdict — those states dominate, because a
    cell whose budget bound tells you nothing about the model. Disagreement among otherwise
    valid samples is UNSTABLE: the honest name for a result that does not reproduce. Never a
    majority vote — a 1-of-2 pass at temperature 0 is a boundary artifact, not a capability.
    """
    if any(v == _ERR for v in samples):
        return _ERR
    if any(v == _TRUNC for v in samples):
        return _TRUNC
    if all(v == _PASS for v in samples):
        return _PASS
    if all(v == _FAIL for v in samples):
        return _FAIL
    return _UNSTABLE


def _cell(device, model_id, query, timeout, budget, samples, detail, phase):
    """Measure one (model, query, budget) cell `samples` times. Returns the aggregate verdict."""
    verdicts = []
    for i in range(samples):
        verdict, reply = _ask(device, model_id, query, timeout, budget)
        verdicts.append(verdict)
        detail.append({"phase": phase, "model": model_id, "query": query.id, "band": query.band,
                       "budget": budget, "sample": i, "verdict": verdict, "reply": reply[-300:]})
    return _aggregate(verdicts), verdicts


def _selected_models(args, models, connections, sources):
    chosen = []
    for spec in models.all():
        src = _cheapest_source(spec.model_id, connections, sources)
        if src is None:
            continue  # no connection: unreachable even by a pin
        gate = _GATE.get(getattr(src, "cost_class", "token_direct"), "include_paid")
        if gate and not getattr(args, gate):
            continue
        chosen.append((spec, src))
    if args.models:
        wanted = set(args.models)
        chosen = [(s, src) for s, src in chosen if s.model_id in wanted]
    return sorted(chosen, key=lambda t: (cost_class_rank(t[1].cost_class), t[0].model_id))


def _capability_pass(device, targets, queries, args, detail):
    """Phase 1 — every (model, query) at the SAME non-binding ceiling budget.

    This is the only phase whose verdicts license a capability claim, and only if the ceiling
    did not bind: a model with ANY truncated cell has no capability verdict at all, because we
    cannot tell "cannot" from "ran out of room". The run reports its own truncation count so
    that failure of the experiment is visible rather than absorbed into the results.
    """
    print(f"── PHASE: capability @ ceiling={args.ceiling} tokens, samples={args.samples}\n")
    cells: dict[str, dict[str, dict]] = defaultdict(dict)
    for spec, src in targets:
        print(f"── {spec.model_id}  @{src.name} ({src.cost_class}, claims '{spec.difficulty_bucket}')")
        for q in queries:
            verdict, samples = _cell(device, spec.model_id, q, args.timeout,
                                     args.ceiling, args.samples, detail, "capability")
            cells[spec.model_id][q.id] = {"band": q.band, "verdict": verdict, "samples": samples,
                                          "budget": args.ceiling, "floor_tokens": None}
            print(f"     {_MARK[verdict]} {q.id:14} {verdict:9} {samples}")
        print()
    return cells


def _budget_walk(device, targets, queries, args, cells, detail):
    """Phase 2 — for each cell that PASSED at the ceiling, walk the budget down to its floor.

    Only a passing cell has a floor to find: a model that cannot answer at 32k will not answer
    at 1k, and re-measuring it would burn GPU to reconfirm a failure. The walk stops at the
    first budget that does not cleanly pass, so the floor is the smallest budget that DID.
    `floor_tokens is None` after a walk means the cell failed even at the ladder's top rung.
    """
    ladder = [b for b in args.walk_ladder if b < args.ceiling]
    walk_bands = tuple(args.walk_bands) if args.walk_bands else BANDS
    print(f"── PHASE: budget walk, ladder={ladder}, bands={walk_bands}, samples={args.walk_samples}\n")
    for spec, _src in targets:
        eligible = [q for q in queries
                    if q.band in walk_bands and cells[spec.model_id].get(q.id, {}).get("verdict") == _PASS]
        if not eligible:
            print(f"── {spec.model_id}: no cells passed at the ceiling in {walk_bands} — nothing to walk\n")
            continue
        print(f"── {spec.model_id}")
        for q in eligible:
            floor = args.ceiling  # it passed here; that is the floor until a smaller budget passes
            for budget in ladder:
                verdict, samples = _cell(device, spec.model_id, q, args.timeout,
                                         budget, args.walk_samples, detail, "walk")
                print(f"     {_MARK[verdict]} {q.id:14} @{budget:<6} {verdict:9} {samples}")
                if verdict != _PASS:
                    break
                floor = budget
            cells[spec.model_id][q.id]["floor_tokens"] = floor
            print(f"       └─ floor: {floor} tokens")
        print()
    return cells


def _rollup(targets, queries, cells, bands):
    """Per-model band roll-up + the truncation count that licenses (or voids) its verdicts."""
    models_out = {}
    for spec, src in targets:
        per_band, truncations, errors = {}, 0, 0
        for b in bands:
            vs = [cells[spec.model_id][q.id]["verdict"] for q in queries if q.band == b]
            per_band[b] = {
                "pass": vs.count(_PASS), "total": len(vs), "fail": vs.count(_FAIL),
                "truncated": vs.count(_TRUNC), "errors": vs.count(_ERR),
                "unstable": vs.count(_UNSTABLE),
            }
            truncations += vs.count(_TRUNC)
            errors += vs.count(_ERR)
        models_out[spec.model_id] = {
            "source": src.name, "cost_class": src.cost_class,
            "claimed_bucket": spec.difficulty_bucket,
            "cells": cells[spec.model_id], "bands": per_band,
            # The whole-run guard: any truncation and NO capability verdict is licensed for
            # this model, because the ceiling bound somewhere and we cannot say where.
            "truncations": truncations, "errors": errors,
        }
    return models_out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--include-subscription", action="store_true",
                    help="also query subscription / free-throttled sources (Ollama Pro, Gemini free)")
    ap.add_argument("--include-paid", action="store_true",
                    help="also query token_direct sources — REAL DOLLARS PER QUERY")
    ap.add_argument("--models", nargs="*", default=None, help="restrict to these model ids")
    ap.add_argument("--bands", nargs="*", default=None, help="restrict the corpus to these bands")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--phase", choices=("capability", "both"), default="capability",
                    help="capability = one non-binding ceiling. 'both' adds the budget walk, "
                         "which ALWAYS runs the capability pass first: only a cell that passed "
                         "at the ceiling has a token floor worth finding.")
    ap.add_argument("--ceiling", type=int, default=32768,
                    help="the non-binding budget for the capability pass. If ANY cell truncates, "
                         "this ceiling BOUND — raise it and re-run; do not read the verdicts.")
    ap.add_argument("--samples", type=int, default=2,
                    help="samples per capability cell. >1 detects instability; it is never averaged")
    ap.add_argument("--walk-samples", type=int, default=2, help="samples per budget-walk rung")
    ap.add_argument("--walk-ladder", nargs="*", type=int, default=list(DEFAULT_WALK_LADDER))
    ap.add_argument("--walk-bands", nargs="*", default=None,
                    help="restrict the budget walk to these bands (it is the expensive phase)")
    ap.add_argument("--emit-note", action="store_true", help="write the result to the memory store")
    args = ap.parse_args()

    from unseen_university.devices.inference.device import InferenceDevice

    models, sources = default_models(), default_sources()
    connections = default_connections(models)
    targets = _selected_models(args, models, connections, sources)
    if not targets:
        print("no models selected (all gated off?) — try --include-subscription", file=sys.stderr)
        return 1

    bands = tuple(args.bands) if args.bands else BANDS
    queries = [q for q in CORPUS if q.band in bands]
    if not queries:
        print(f"no queries in bands {bands} (known: {BANDS})", file=sys.stderr)
        return 1

    device = InferenceDevice()
    print(f"corpus: {len(queries)} queries across {len(bands)} band(s) | models: {len(targets)}\n")

    detail: list[dict] = []
    cells = _capability_pass(device, targets, queries, args, detail)
    if args.phase == "both":
        cells = _budget_walk(device, targets, queries, args, cells, detail)

    models_out = _rollup(targets, queries, cells, bands)

    # ── the matrix: pass-rate per band. The band where a model collapses is its frontier. ──
    print("CAPABILITY MATRIX  (pass/total per band)")
    print(f"  measured at ceiling={args.ceiling} tokens, {args.samples} sample(s)/cell, temperature=0")
    print("  '!' = source error, '…' = truncated, '~' = unstable — NONE is a wrong answer")
    header = f"{'model':36} {'claims':9} " + " ".join(f"{b.split('_')[0]:>7}" for b in bands)
    print(header)
    print("-" * len(header))
    for spec, _src in targets:
        row = models_out[spec.model_id]
        cellstr = []
        for b in bands:
            d = row["bands"][b]
            cellstr.append(f"{d['pass']}/{d['total']}" + ("!" if d["errors"] else "")
                           + ("…" if d["truncated"] else "") + ("~" if d["unstable"] else ""))
        print(f"{spec.model_id:36} {spec.difficulty_bucket:9} " + " ".join(f"{c:>7}" for c in cellstr))
        if row["truncations"]:
            print(f"{'':36} ⚠ {row['truncations']} truncated cell(s) — the ceiling BOUND; "
                  f"no capability verdict is licensed for this model at {args.ceiling}")

    floors = {m: {q: c["floor_tokens"] for q, c in r["cells"].items() if c["floor_tokens"]}
              for m, r in models_out.items()}
    if any(floors.values()):
        print("\nTOKEN FLOORS (smallest budget that still passed)")
        for m, qs in floors.items():
            if qs:
                print(f"  {m:34} " + "  ".join(f"{q}={t}" for q, t in sorted(qs.items())))

    if args.emit_note:
        body = {
            "title": f"Capability ceiling sweep @ {args.ceiling} tokens",
            "text": "Capability measured at a NON-BINDING token ceiling, so a wrong answer means "
                    "'cannot' and not 'ran out of room'. A cell is PASS only if every sample "
                    "passed and none truncated; disagreement is UNSTABLE, never a majority-vote "
                    "pass. `truncations > 0` for a model means the ceiling bound and NO capability "
                    "verdict is licensed for it. `floor_tokens` is the smallest budget that still "
                    "passed — a COST property, not a capability one. Every number here is true "
                    "only for these conditions.",
            "conditions": {
                "ceiling_tokens": args.ceiling,
                "samples_per_cell": args.samples,
                "walk_samples": args.walk_samples,
                "temperature": 0.0,
                # The timeout is a CONDITION, not plumbing. Measured 2026-07-09:
                # deepseek-v3.1:671b-cloud twice hit the 600s limit on b5-frobenius and was
                # recorded as 'no verdict' by a number nobody had written down. A controlled
                # probe (same query, same 1800s timeout, only max_tokens varied) then showed the
                # model finishes at BOTH 4096 (260s) and 32768 (195s) — so those timeouts were
                # transient cloud latency, not the ceiling. Both facts are worth keeping: a
                # timeout silently converts a capable model into no-verdict, AND the first
                # explanation that fits the data is not thereby true.
                "timeout_s": args.timeout,
                "measured_on": date.today().isoformat(),
                "corpus_size": len(queries),
                "bands": list(bands),
                "phase": args.phase,
                "instrument": "devlab/claudecode/escalation_matrix.py",
            },
            "models": models_out,
            "detail": detail,
        }
        tmp = Path("/tmp/escalation_matrix.json")
        tmp.write_text(json.dumps(body, indent=2))
        tools = Path(uu_root()) / "devlab" / "claudecode"
        subprocess.run([sys.executable, str(tools / "memory_emit.py"), "--category", "notes",
                        "--emitter", "cc.0", "--kind", "note", "--namespace",
                        NOTE_NAMESPACE, "--body-file", str(tmp)], check=True)
        print(f"\nnote emitted under namespace '{NOTE_NAMESPACE}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

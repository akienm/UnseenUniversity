#!/usr/bin/env python3
"""
escalation_matrix.py — run the escalation corpus against each model; emit the capability matrix.

T-inference-escalation-eval-corpus (the live half). This is a SMOKE RUN that produces DATA.
It is not a test and it asserts nothing: it reaches real providers, so its outcome depends on
whether Hex is up and whether a subscription is live. Asserting on that is how a proof goes
green on the weather (2026-07-08).

What it measures
----------------
`ModelSpec.difficulty_bucket` is a hand-typed CLAIM, and the cost-first selector REWARDS
overclaiming: a cheap model that claims the top bucket wins every bucket beneath it too
(T-inference-cost-first-sort-strands-cloud-fleet). This script checks the claim. Each model is
PINNED (bypassing routing) and asked every corpus query; the band where its pass-rate collapses
is its real capability frontier.

Cost safety (CP6)
-----------------
Models are grouped by their source's cost_class and the expensive ones are OFF by default:
  owned_local   — on by default (Hex; free at the margin)
  subscription  — --include-subscription (Ollama Pro; already sunk, no per-token charge)
  token_direct  — --include-paid (Anthropic; REAL DOLLARS PER QUERY)
free_throttled sits with subscription. Nothing paid runs unless you ask for it by name.

Usage
-----
  python3 devlab/claudecode/escalation_matrix.py                        # local only
  python3 devlab/claudecode/escalation_matrix.py --include-subscription
  python3 devlab/claudecode/escalation_matrix.py --include-paid --emit-note
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
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
_PASS, _FAIL, _ERR, _TRUNC = "PASS", "fail", "ERR", "TRUNC"


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


def _unclosed_think(text: str) -> bool:
    """An opened <think> with no closing tag: the reply was cut off inside the scratchpad."""
    low = (text or "").lower()
    return "<think" in low and "</think" not in low


def _ask(device, model_id, query, timeout, max_tokens):
    """Pin `model_id` and ask one corpus query. Returns (verdict, reply_text)."""
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
    if resp.finish_reason == "length" or _unclosed_think(text):
        return _TRUNC, text
    return _FAIL, text


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
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--include-subscription", action="store_true",
                    help="also query subscription / free-throttled sources (Ollama Pro, Gemini free)")
    ap.add_argument("--include-paid", action="store_true",
                    help="also query token_direct sources — REAL DOLLARS PER QUERY")
    ap.add_argument("--models", nargs="*", default=None, help="restrict to these model ids")
    ap.add_argument("--bands", nargs="*", default=None, help="restrict to these bands")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--max-tokens", type=int, default=4096,
                    help="reasoning models spend budget inside <think>; too low fabricates failures")
    ap.add_argument("--emit-note", action="store_true", help="write the matrix to the memory store")
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

    # model_id -> band -> [verdicts]
    matrix: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    detail: list[dict] = []

    for spec, src in sorted(targets, key=lambda t: (cost_class_rank(t[1].cost_class), t[0].model_id)):
        print(f"── {spec.model_id}  @{src.name} ({src.cost_class}, claims '{spec.difficulty_bucket}')")
        for q in queries:
            verdict, reply = _ask(device, spec.model_id, q, args.timeout, args.max_tokens)
            matrix[spec.model_id][q.band].append(verdict)
            detail.append({"model": spec.model_id, "source": src.name, "query": q.id,
                           "band": q.band, "verdict": verdict, "reply": reply[-300:]})
            mark = {"PASS": "✓", "fail": "✗", "ERR": "!", "TRUNC": "…"}[verdict]
            print(f"     {mark} {q.id:14} {verdict}")
        print()

    # ── the matrix: pass-rate per band. The band where a model collapses is its frontier. ──
    print("CAPABILITY MATRIX  (pass/total per band)")
    print("  '!' = source error, '…' = truncated mid-<think> — NEITHER is a wrong answer")
    header = f"{'model':36} {'claims':9} " + " ".join(f"{b.split('_')[0]:>6}" for b in bands)
    print(header)
    print("-" * len(header))
    rows = []
    for spec, src in sorted(targets, key=lambda t: (cost_class_rank(t[1].cost_class), t[0].model_id)):
        cells, row = [], {"model": spec.model_id, "source": src.name,
                          "claimed_bucket": spec.difficulty_bucket, "bands": {}}
        for b in bands:
            vs = matrix[spec.model_id][b]
            npass, nerr, ntr = vs.count(_PASS), vs.count(_ERR), vs.count(_TRUNC)
            cells.append(f"{npass}/{len(vs)}" + ("!" if nerr else "") + ("…" if ntr else ""))
            row["bands"][b] = {"pass": npass, "total": len(vs), "errors": nerr, "truncated": ntr}
        rows.append(row)
        print(f"{spec.model_id:36} {spec.difficulty_bucket:9} " + " ".join(f"{c:>6}" for c in cells))

    if args.emit_note:
        body = {"title": "Escalation capability matrix",
                "text": "Measured pass-rate per structural band, per PINNED model. The band where a "
                        "model's pass-rate collapses is its real capability frontier — compare "
                        "against `claimed_bucket`, which is hand-typed and unverified.",
                "rows": rows, "detail": detail}
        tmp = Path("/tmp/escalation_matrix.json")
        tmp.write_text(json.dumps(body, indent=2))
        tools = Path(uu_root()) / "devlab" / "claudecode"
        subprocess.run([sys.executable, str(tools / "memory_emit.py"), "--category", "notes",
                        "--emitter", "cc.0", "--kind", "note", "--namespace",
                        "escalation-capability-matrix", "--body-file", str(tmp)], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

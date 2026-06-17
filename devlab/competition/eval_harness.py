"""
eval_harness.py — Inference competition race runner.

Scores both classifiers (k-NN and LLM) against the holdout set.
Accuracy = fraction of holdout rows where classifier agrees with the
memory_type assigned by the book-reading pipeline (ground truth label).

Usage:
    python lab/competition/eval_harness.py

Output: scorecard printed to stdout. Deterministic — same holdout, same result.

Notes:
- Holdout rows only (holdout=true in competition.memories).
- Per-row verdicts are logged for debugging.
- Never modifies holdout labels.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import psycopg2

_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def _conn():
    return psycopg2.connect(_DB_URL)


def _fetch_holdout() -> list[tuple[str, str, str]]:
    """Return [(id, narrative, memory_type)] for all holdout rows."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, narrative, memory_type "
                "FROM competition.memories "
                "WHERE holdout = true AND narrative IS NOT NULL "
                "  AND memory_type IS NOT NULL "
                "ORDER BY id"
            )
            return cur.fetchall()
    finally:
        conn.close()


def _run_classifier(
    classify_fn,
    rows: list[tuple[str, str, str]],
    classifier_name: str,
    verbose: bool = False,
) -> dict:
    """Run classify_fn on every row; return accuracy + cost stats."""
    correct = total = 0
    total_cloud_calls = 0

    for mem_id, narrative, ground_truth in rows:
        try:
            predicted, cloud_calls = classify_fn(narrative)
        except Exception as e:
            predicted, cloud_calls = "FACTUAL", 0
            if verbose:
                print(f"  [{classifier_name}] ERROR on {mem_id}: {e}")

        is_correct = predicted == ground_truth
        correct += int(is_correct)
        total += 1
        total_cloud_calls += cloud_calls

        if verbose:
            verdict = "✓" if is_correct else "✗"
            print(
                f"  [{classifier_name}] {verdict} {mem_id[:20]:20s} "
                f"truth={ground_truth:14s} pred={predicted:14s} "
                f"cloud={cloud_calls}"
            )

    accuracy = (correct / total * 100) if total else 0.0
    return {
        "name": classifier_name,
        "total": total,
        "correct": correct,
        "accuracy_pct": round(accuracy, 1),
        "total_cloud_calls": total_cloud_calls,
    }


def run_race(verbose: bool = False) -> dict:
    """Race both classifiers on the holdout set.

    Returns {"knn": {...}, "llm": {...}, "holdout_rows": N}.
    """
    from devlab.competition.classifiers.knn_classifier import classify as knn_classify
    from devlab.competition.classifiers.llm_classifier import classify as llm_classify

    rows = _fetch_holdout()
    if not rows:
        return {"error": "no holdout rows found — run carve_holdout.py first", "holdout_rows": 0}

    knn_stats = _run_classifier(knn_classify, rows, "knn", verbose=verbose)
    llm_stats = _run_classifier(llm_classify, rows, "llm", verbose=verbose)

    return {
        "holdout_rows": len(rows),
        "knn": knn_stats,
        "llm": llm_stats,
    }


def print_scorecard(result: dict) -> None:
    """Print a human-readable scorecard from run_race() output."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    n = result["holdout_rows"]
    knn = result["knn"]
    llm = result["llm"]

    print(f"\n{'='*56}")
    print(f"  INFERENCE COMPETITION SCORECARD — {n} holdout rows")
    print(f"{'='*56}")
    print(f"  {'Classifier':<12}  {'Accuracy':>10}  {'Cloud calls':>12}")
    print(f"  {'-'*12}  {'-'*10}  {'-'*12}")
    print(f"  {'k-NN':<12}  {knn['accuracy_pct']:>9.1f}%  {knn['total_cloud_calls']:>12}")
    print(f"  {'LLM':<12}  {llm['accuracy_pct']:>9.1f}%  {llm['total_cloud_calls']:>12}")
    print(f"{'='*56}")

    # Winner determination
    if knn["accuracy_pct"] > llm["accuracy_pct"]:
        winner = "k-NN wins on accuracy"
    elif llm["accuracy_pct"] > knn["accuracy_pct"]:
        winner = "LLM wins on accuracy"
    else:
        winner = "Tie on accuracy"
        if knn["total_cloud_calls"] < llm["total_cloud_calls"]:
            winner += " — k-NN wins on cost"
        elif llm["total_cloud_calls"] < knn["total_cloud_calls"]:
            winner += " — LLM wins on cost"

    print(f"\n  VERDICT: {winner}")
    print()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="Print per-row verdicts")
    args = parser.parse_args()

    if args.verbose:
        print("Running verbose race (per-row verdicts)...")
    result = run_race(verbose=args.verbose)
    print_scorecard(result)


if __name__ == "__main__":
    main()

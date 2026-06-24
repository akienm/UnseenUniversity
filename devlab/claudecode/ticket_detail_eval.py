#!/usr/bin/env python3
# author-model: sonnet
"""Phase 1 eval: does ticket detail correlate with build cost?

Reads sprint_tokens.log for build cost (output_tokens per ticket), fetches
ticket descriptions from the DB, computes a detail_score proxy, and reports
Spearman rank correlation with a clear yes/no recommendation.

Usage:
  python3 ticket_detail_eval.py          # full analysis, text report
  python3 ticket_detail_eval.py --json   # JSON output for programmatic use

Build cost metric: output_tokens (most expensive token class; best proxy for
CC generation effort). Secondary: input_tokens for sanity check.

Detail proxy: description_char_count (describes scope depth) + 10 per concrete
Affected-files section (real paths vs TBD) + 10 per real Completion-criteria
section (specific criteria vs absent/generic).

Decision rule: Spearman |r| >= 0.30 AND p < 0.10 → correlation present (proceed
to Phase 2). Below threshold → no evidence to justify Phase 2.
"""

from __future__ import annotations
from unseen_university._uu_root import uu_home

import json
import os
import re
import sys
from pathlib import Path

from unseen_university import ticket_store

IGOR_HOME = Path(uu_home())
SPRINT_LOG = IGOR_HOME / "claudecode" / "sprint_tokens.log"

CORRELATION_THRESHOLD = 0.30
P_THRESHOLD = 0.10


# ── Data loading ──────────────────────────────────────────────────────────────

def _read_sprint_log() -> list[dict]:
    """Parse sprint_tokens.log. Returns list of dicts, one per line."""
    if not SPRINT_LOG.exists():
        return []
    rows = []
    for line in SPRINT_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        try:
            rows.append({
                "ts": parts[0],
                "ticket_id": parts[1],
                "input_tokens": int(parts[2]),
                "cache_create": int(parts[3]),
                "cache_read": int(parts[4]),
                "output_tokens": int(parts[5]),
                "model": parts[6],
            })
        except (ValueError, IndexError):
            continue
    return rows


def _fetch_ticket_meta(ticket_ids: list[str]) -> dict[str, dict]:
    """Fetch description + reset_count for each ticket from the filesystem store.

    Returns {ticket_id: {description, reset_count}} for found tickets. Includes
    closed tickets — the eval correlates against the sprint log, which is
    dominated by completed work.
    """
    wanted = set(ticket_ids)
    result = {}
    for t in ticket_store.list(include_closed=True):
        tid = t.get("id", "")
        if tid in wanted:
            result[tid] = {
                "description": t.get("description") or "",
                "reset_count": int(t.get("reset_count") or 0),
                "size": t.get("size") or "?",
            }
    return result


# ── Detail scoring ────────────────────────────────────────────────────────────

_AFFECTED_FILES_RE = re.compile(
    r"\*\*Affected files:\*\*(.*?)(?=\n\*\*[A-Za-z]|\Z)", re.DOTALL | re.IGNORECASE
)
_CRITERIA_RE = re.compile(
    r"\*\*Completion criteria:\*\*(.*?)(?=\n\*\*[A-Za-z]|\Z)", re.DOTALL | re.IGNORECASE
)


def _has_concrete_affected_files(description: str) -> bool:
    """True if Affected files section contains real paths, not just 'TBD' or generic."""
    m = _AFFECTED_FILES_RE.search(description)
    if not m:
        return False
    body = m.group(1).strip()
    if not body or body.lower() in ("tbd", "none", "n/a"):
        return False
    # Must contain something that looks like a file path or module reference
    return bool(re.search(r"[\w/]+\.\w+|[\w/]+/[\w/]+", body))


def _has_real_criteria(description: str) -> bool:
    """True if Completion criteria section is non-empty and non-trivial."""
    m = _CRITERIA_RE.search(description)
    if not m:
        return False
    body = m.group(1).strip()
    return len(body) >= 20  # at least a short sentence


def detail_score(description: str) -> dict:
    """Return detail proxy breakdown for a ticket description."""
    desc_chars = len(description)
    concrete_files = _has_concrete_affected_files(description)
    real_criteria = _has_real_criteria(description)
    score = desc_chars / 50 + (10 if concrete_files else 0) + (10 if real_criteria else 0)
    return {
        "score": round(score, 1),
        "desc_chars": desc_chars,
        "concrete_files": concrete_files,
        "real_criteria": real_criteria,
    }


# ── Statistics (pure Python — no scipy/numpy) ─────────────────────────────────

def _rank(values: list[float]) -> list[float]:
    """Return ranks (1-based, average for ties)."""
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    n = len(indexed)
    while i < n:
        j = i
        while j < n - 1 and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def spearman_r(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation coefficient."""
    n = len(x)
    if n < 3:
        return float("nan")
    rx = _rank(x)
    ry = _rank(y)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    den_x = sum((rx[i] - mean_rx) ** 2 for i in range(n)) ** 0.5
    den_y = sum((ry[i] - mean_ry) ** 2 for i in range(n)) ** 0.5
    if den_x == 0 or den_y == 0:
        return float("nan")
    return num / (den_x * den_y)


def approx_p_value(r: float, n: int) -> float:
    """Approximate two-tailed p-value using normal approximation to the t-distribution.

    t = r * sqrt((n-2) / (1-r^2)), then p ≈ erfc(|t| / sqrt(2)).
    The t-distribution converges to normal for n >= 20; for smaller n the
    p-value is slightly underestimated. Accurate to within ~15% for n >= 15.
    """
    import math
    if n < 3 or r != r:  # NaN check
        return float("nan")
    if abs(r) >= 1.0:
        return 0.0
    t = r * ((n - 2) / max(1 - r ** 2, 1e-10)) ** 0.5
    # erfc(|t|/sqrt(2)) is the two-tailed p-value under the normal approximation
    return math.erfc(abs(t) / math.sqrt(2))


# ── Analysis ──────────────────────────────────────────────────────────────────

def run_analysis(json_output: bool = False) -> dict:
    """Run Phase 1 correlation analysis. Returns result dict."""
    log_rows = _read_sprint_log()
    if not log_rows:
        return {"error": f"sprint_tokens.log not found at {SPRINT_LOG}"}

    # Deduplicate: if same ticket appears multiple times, take the LAST entry
    # (most recent sprint = most representative build cost)
    seen: dict[str, dict] = {}
    for row in log_rows:
        seen[row["ticket_id"]] = row
    unique_rows = list(seen.values())

    ticket_ids = [r["ticket_id"] for r in unique_rows]
    meta = _fetch_ticket_meta(ticket_ids)

    rows_with_meta = []
    skipped_no_meta = []
    for row in unique_rows:
        tid = row["ticket_id"]
        if tid not in meta:
            skipped_no_meta.append(tid)
            continue
        m = meta[tid]
        ds = detail_score(m["description"])
        rows_with_meta.append({
            "ticket_id": tid,
            "output_tokens": row["output_tokens"],
            "input_tokens": row["input_tokens"],
            "detail_score": ds["score"],
            "desc_chars": ds["desc_chars"],
            "concrete_files": ds["concrete_files"],
            "real_criteria": ds["real_criteria"],
            "reset_count": m["reset_count"],
            "size": m["size"],
        })

    n = len(rows_with_meta)
    if n < 5:
        return {
            "error": f"Only {n} tickets with metadata — need at least 5 for meaningful analysis",
            "skipped": skipped_no_meta,
        }

    detail_scores = [r["detail_score"] for r in rows_with_meta]
    build_costs = [r["output_tokens"] for r in rows_with_meta]
    reset_counts = [r["reset_count"] for r in rows_with_meta]

    r_detail_cost = spearman_r(detail_scores, build_costs)
    r_detail_reset = spearman_r(detail_scores, reset_counts)
    r_cost_reset = spearman_r(build_costs, reset_counts)

    p_detail_cost = approx_p_value(r_detail_cost, n)
    p_detail_reset = approx_p_value(r_detail_reset, n)

    # The hypothesis predicts r < 0 (more detail → lower cost).
    # A positive r means the opposite: more detail correlates with higher cost.
    # This is likely a SIZE confound — bigger tickets are both more detailed AND
    # more expensive to build. The hypothesis is only supported by r < -0.30.
    reset_has_variance = len(set(reset_counts)) > 1
    r_detail_cost_nan = r_detail_cost != r_detail_cost
    r_detail_reset_nan = r_detail_reset != r_detail_reset

    hypothesis_supported_cost = (
        not r_detail_cost_nan
        and r_detail_cost <= -CORRELATION_THRESHOLD
        and p_detail_cost < P_THRESHOLD
    )
    hypothesis_supported_reset = (
        reset_has_variance
        and not r_detail_reset_nan
        and r_detail_reset <= -CORRELATION_THRESHOLD
        and p_detail_reset < P_THRESHOLD
    )
    wrong_direction_cost = (
        not r_detail_cost_nan
        and r_detail_cost >= CORRELATION_THRESHOLD
    )

    proceed_to_phase2 = hypothesis_supported_cost or hypothesis_supported_reset

    result = {
        "n": n,
        "skipped_no_meta": skipped_no_meta,
        "rows": rows_with_meta,
        "spearman_r_detail_vs_output_tokens": round(r_detail_cost, 3) if not r_detail_cost_nan else None,
        "p_detail_vs_output_tokens": round(p_detail_cost, 3) if p_detail_cost == p_detail_cost else None,
        "spearman_r_detail_vs_reset_count": round(r_detail_reset, 3) if not r_detail_reset_nan else None,
        "p_detail_vs_reset_count": round(p_detail_reset, 3) if p_detail_reset == p_detail_reset else None,
        "spearman_r_cost_vs_reset_count": round(r_cost_reset, 3) if r_cost_reset == r_cost_reset else None,
        "reset_has_variance": reset_has_variance,
        "correlation_threshold": CORRELATION_THRESHOLD,
        "p_threshold": P_THRESHOLD,
        "finding_cost": (
            "WRONG DIRECTION (size confound)" if wrong_direction_cost
            else "HYPOTHESIS SUPPORTED" if hypothesis_supported_cost
            else "NO CORRELATION"
        ),
        "finding_reset": (
            "NO VARIANCE (all resets=0)" if not reset_has_variance
            else "HYPOTHESIS SUPPORTED" if hypothesis_supported_reset
            else "NO CORRELATION"
        ),
        "recommendation": "PROCEED to Phase 2" if proceed_to_phase2 else "STOP — do not build Opus ticket pipeline",
    }

    if json_output:
        return result

    _print_report(result)
    return result


def _print_report(r: dict) -> None:
    if "error" in r:
        print(f"ERROR: {r['error']}")
        return

    print("=" * 60)
    print("Phase 1 Eval: Ticket Detail vs Build Cost")
    print("=" * 60)
    print(f"N = {r['n']} tickets (deduplicated from sprint_tokens.log)")
    if r["skipped_no_meta"]:
        print(f"Skipped (no queue metadata): {len(r['skipped_no_meta'])} tickets")
    print()

    # Summary table
    rows = sorted(r["rows"], key=lambda x: x["output_tokens"], reverse=True)
    print(f"{'Ticket':<40} {'Size':>4} {'OutTok':>7} {'Detail':>7} {'Reset':>5}")
    print("-" * 65)
    for row in rows[:20]:  # top 20 by build cost
        print(
            f"{row['ticket_id']:<40} {row['size']:>4} "
            f"{row['output_tokens']:>7,} {row['detail_score']:>7.1f} "
            f"{row['reset_count']:>5}"
        )
    if len(rows) > 20:
        print(f"  ... ({len(rows) - 20} more rows in --json output)")
    print()

    print("Detail proxy = description_chars/50 + 10*has_concrete_files + 10*has_criteria")
    print()
    def _fmt_r(v) -> str:
        return f"{v:+.3f}" if v is not None else "  N/A"

    print("Correlation results:")
    print(f"  Detail score vs output_tokens:  r = {_fmt_r(r['spearman_r_detail_vs_output_tokens'])}  "
          f"(p ≈ {r['p_detail_vs_output_tokens']})  → {r['finding_cost']}")
    print(f"  Detail score vs reset_count:    r = {_fmt_r(r['spearman_r_detail_vs_reset_count'])}  "
          f"(p ≈ {r['p_detail_vs_reset_count']})  → {r['finding_reset']}")
    print(f"  Build cost vs reset_count:      r = {_fmt_r(r['spearman_r_cost_vs_reset_count'])}  (context)")
    print()
    print(f"Threshold: |r| >= {r['correlation_threshold']} AND p < {r['p_threshold']}")
    print()
    print("─" * 60)
    print(f"RECOMMENDATION: {r['recommendation']}")
    print("─" * 60)

    if r["recommendation"].startswith("STOP"):
        print()
        if r["finding_cost"] == "WRONG DIRECTION (size confound)":
            print("Reasoning: Detail correlates POSITIVELY with build cost (r > 0).")
            print("This is the WRONG direction — more detail predicts MORE cost,")
            print("not less. Likely size confound: bigger/harder tickets are both")
            print("more detailed AND more expensive to build. Ticket detail does")
            print("not predict build efficiency in this dataset.")
        else:
            print("Reasoning: No significant correlation between ticket detail")
            print("and build cost or reset rate. Richer tickets don't reliably")
            print("build faster or with fewer resets. The bottleneck is likely")
            print("elsewhere (verification, scope clarity, test setup).")
        if not r.get("reset_has_variance"):
            print("Note: reset_count is 0 for all tracked tickets — that axis")
            print("cannot be evaluated until some tickets accumulate resets.")
        print("File a new ticket if future data (N > 50, resets present)")
        print("changes this picture.")
    else:
        print()
        print("Reasoning: Statistically meaningful NEGATIVE correlation found.")
        print("More detailed tickets build cheaper/with fewer resets.")
        print("File T-opus-ticket-pipeline for Phase 2 eval.")


def main() -> None:
    json_output = "--json" in sys.argv
    result = run_analysis(json_output=json_output)
    if json_output:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

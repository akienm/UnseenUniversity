"""verify_retrieval — confirm a deposited engram surfaces for target queries.

After deposit_engram, the engineer needs to verify the engram is actually
retrievable for the query shapes it's meant to answer. This tool runs
cortex.search() for each target query and reports whether the target
engram_id surfaces within top_k, along with its rank position.

Usage:
  from devlab.claudecode.engram_tools.verify_retrieval import verify, VerifyResult
  results = verify(
      engram_id="20260423093501xxx",
      queries=["can Igor execute code from the web channel",
               "is the channel a capability gate"],
      cortex=live_cortex,
      top_k=5,
  )
  for r in results:
      print(r.query, "PASS" if r.found else "FAIL", "rank=", r.rank)

Pass/fail summary:
  all_pass(results) → True if every query found the engram within top_k.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


class _CortexLike(Protocol):
    """Minimal cortex surface: .search(query, limit) returning iterable of Memory-like."""

    def search(self, query: str, limit: int) -> list: ...


DEFAULT_TOP_K = 5


@dataclass
class VerifyResult:
    """Per-query verification result.

    query — the query that was issued
    found — True if target_engram_id was in top_k results
    rank — 1-indexed position in the results (None if not found)
    top_memory_ids — all ids returned, in order, for context
    """

    query: str
    found: bool
    rank: Optional[int]
    top_memory_ids: list[str] = field(default_factory=list)


def _extract_id(result_item: Any) -> Optional[str]:
    """Accept either a Memory dataclass (.id), a dict ({'id': ...}), or a
    string id. Returns the id or None if not extractable."""
    if isinstance(result_item, str):
        return result_item
    if isinstance(result_item, dict):
        return result_item.get("id")
    return getattr(result_item, "id", None)


def verify(
    engram_id: str,
    queries: list[str],
    cortex: _CortexLike,
    top_k: int = DEFAULT_TOP_K,
) -> list[VerifyResult]:
    """Run each query against cortex.search, report whether engram_id
    surfaces in top_k. Returns one VerifyResult per query.

    cortex.search is called with limit=top_k. If cortex.search returns more
    than top_k, we still only check the first top_k (belt-and-suspenders).
    """
    if not engram_id:
        raise ValueError("engram_id is empty")
    if not queries:
        raise ValueError("queries list is empty")
    if top_k <= 0:
        raise ValueError(f"top_k must be > 0, got {top_k}")

    results: list[VerifyResult] = []
    for query in queries:
        raw = cortex.search(query, limit=top_k) or []
        ids: list[str] = []
        for item in list(raw)[:top_k]:
            mid = _extract_id(item)
            if mid:
                ids.append(mid)
        rank: Optional[int] = None
        for idx, mid in enumerate(ids, start=1):
            if mid == engram_id:
                rank = idx
                break
        results.append(
            VerifyResult(
                query=query,
                found=rank is not None,
                rank=rank,
                top_memory_ids=ids,
            )
        )
    return results


def all_pass(results: list[VerifyResult]) -> bool:
    """Shortcut: True iff every VerifyResult.found is True."""
    return all(r.found for r in results) if results else False


def render(results: list[VerifyResult]) -> str:
    """Human-readable summary for stdout/logs."""
    lines: list[str] = []
    passed = sum(1 for r in results if r.found)
    lines.append(f"verify: {passed}/{len(results)} queries found the engram")
    for r in results:
        status = "PASS" if r.found else "FAIL"
        rank_str = f" rank={r.rank}" if r.rank else ""
        lines.append(f"  [{status}]{rank_str}  {r.query}")
        if not r.found:
            lines.append(f"    top ids: {r.top_memory_ids}")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _cli(argv: list[str]) -> int:
    import argparse
    import json
    import os
    import sys
    from pathlib import Path

    ap = argparse.ArgumentParser(
        description="Verify a deposited engram surfaces for target queries.",
    )
    ap.add_argument("engram_id", help="Memory id of the deposited engram")
    ap.add_argument(
        "--queries",
        required=True,
        help="Path to JSON file with a list of target query strings.",
    )
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = ap.parse_args(argv)

    with open(args.queries) as f:
        queries = json.load(f)
    if not isinstance(queries, list):
        print("queries file must contain a JSON list of strings", file=sys.stderr)
        return 2

    db_path_str = os.environ.get("IGOR_DB_PATH")
    if not db_path_str:
        print("IGOR_DB_PATH must be set for live verify.", file=sys.stderr)
        return 2

    from unseen_university.devices.igor.memory.cortex import Cortex

    cortex = Cortex(Path(db_path_str), instance_id=os.environ.get("IGOR_INSTANCE_ID"))
    results = verify(args.engram_id, queries, cortex=cortex, top_k=args.top_k)
    print(render(results))
    return 0 if all_pass(results) else 1


if __name__ == "__main__":
    import sys

    sys.exit(_cli(sys.argv[1:]))

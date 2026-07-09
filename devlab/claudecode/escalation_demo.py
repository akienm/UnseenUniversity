#!/usr/bin/env python3
"""
escalation_demo.py — run ONE corpus query live through GeneralDomain's escalation walk.

"We build out a set of queries that lower level models will just have to escalate on. Once
that works, we come back to this." (Akien, 2026-07-09.) This is the "once that works" check.

It is deliberately NOT a test. It routes for real (no pin), so which model answers depends on
the live rack; asserting on that would be a proof that passes on the weather. It prints what
happened and exits 0 either way. Read the output.

What a working escalation looks like:
    hop 0  ->  <cheap model>   answer_check FAILED   (a confident wrong answer)
    hop 1  ->  <stronger model> answer_check PASSED
    RESULT: correct

What a topped-out ladder looks like — equally real data, not a bug:
    hop 0  ->  <cheap model>   FAILED
    hop 1  ->  <stronger model> FAILED
    HALT: capability ceiling   (the reachable ladder cannot answer this query)

max_tokens defaults high on purpose: a reasoning model spends its budget inside <think>, and a
truncated scratchpad looks exactly like a wrong answer to the verifier. Misreading truncation
as a capability frontier is the same error as trusting a done-envelope without checking the
side effect.

Usage:
  python3 devlab/claudecode/escalation_demo.py --query b3-race
  python3 devlab/claudecode/escalation_demo.py --list
"""

from __future__ import annotations

import argparse
import logging
import sys

from unseen_university.devices.inference.domains.escalation_corpus import (
    ANSWER_INSTRUCTION,
    CORPUS,
)
from unseen_university.devices.inference.domains.general import GeneralDomain


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one corpus query through the live escalation walk.")
    ap.add_argument("--query", help="corpus query id (see --list)")
    ap.add_argument("--list", action="store_true", help="list query ids and bands")
    ap.add_argument("--max-tokens", type=int, default=4096)
    args = ap.parse_args()

    if args.list or not args.query:
        for q in CORPUS:
            print(f"{q.id:14} {q.band:24} answer={q.answer!r}")
        return 0

    matches = [q for q in CORPUS if q.id == args.query]
    if not matches:
        print(f"no such query: {args.query}", file=sys.stderr)
        return 1
    query = matches[0]

    # The walk logs each crossing at INFO: dispatch|hop=N, check|passed=..., escalate|hop→N.
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    for noisy in ("urllib3", "httpx", "psycopg2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    print(f"\nQUERY  {query.id}  (band={query.band})")
    print(f"GROUND TRUTH  {query.answer!r}   (a failing model typically says {query.confabulation!r})")
    print("Routing is UNPINNED: the rack picks the model at each rung.\n")

    # Ground truth IS the answer check. At runtime, with no ground truth, this is the open
    # question the general domain names rather than pretends to have solved.
    domain = GeneralDomain(answer_check=query.verify, max_tokens=args.max_tokens)
    result = domain.ask(f"{query.prompt}\n\n{ANSWER_INSTRUCTION}", query_id=f"demo:{query.id}")

    print("\n" + "=" * 72)
    if result is None:
        print("HALT — the walk topped out without a verified answer.")
        print("If both rungs FAILED the check, the reachable ladder cannot answer this query.")
        print("That is data about the LADDER, not a bug in the walk.")
    else:
        print("VERIFIED ANSWER returned by the walk:")
        print(result.strip()[-400:])
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""uurecall — librarian full-text recall from the command line.

Usage: uurecall.py <query words...>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from devices.librarian.recall import recall


def main(argv):
    if not argv:
        print("usage: uurecall <query>", file=sys.stderr)
        sys.exit(1)
    query = " ".join(argv)
    result = recall(query, limit=10)
    if not result.hits:
        print(f"recall: no results for {query!r}")
        return
    for hit in result.hits:
        score = f"{hit.score:.3f}" if hit.score is not None else "n/a"
        print(f"[{score}] {hit.memory_id}")
        print(f"  {hit.narrative[:200]}")
        print()


if __name__ == "__main__":
    main(sys.argv[1:])

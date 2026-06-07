#!/usr/bin/env python3
"""uuresearch — librarian research-and-summarize from the command line.

Usage: uuresearch.py <query words...>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from unseen_university.devices.librarian.tools.research_tools import research


def main(argv):
    if not argv:
        print("usage: uuresearch <query>", file=sys.stderr)
        sys.exit(1)
    query = " ".join(argv)
    raw = research(query)
    result = json.loads(raw)
    print(f"Research: {result.get('query', query)}")
    print(f"Model: {result.get('model', '?')}  Tier: {result.get('tier', '?')}")
    print()
    print(result.get("answer", "(no answer)"))
    sources = result.get("sources") or []
    if sources:
        print()
        print(f"Sources ({len(sources)}):")
        for s in sources[:5]:
            print(f"  {s}")


if __name__ == "__main__":
    main(sys.argv[1:])

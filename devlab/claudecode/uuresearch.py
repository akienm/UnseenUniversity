#!/usr/bin/env python3
"""uuresearch — librarian full-text search (palace + indexed + git) from the command line.

Usage: uuresearch.py <query words...>
       uuresearch.py --source palace|indexed|git <query words...>
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from unseen_university.devices.librarian.tools.search_tool import search


def main(argv):
    parser = argparse.ArgumentParser(prog="uuresearch", add_help=False)
    parser.add_argument("--source", default=None, choices=["palace", "indexed", "git"])
    parser.add_argument("query", nargs="*")
    args = parser.parse_args(argv)

    if not args.query:
        print("usage: uuresearch [--source palace|indexed|git] <query words...>", file=sys.stderr)
        sys.exit(1)

    query = " ".join(args.query)
    results = asyncio.run(search(query, source=args.source, limit=15))

    if not results:
        print(f"uuresearch: no results for {query!r}")
        return

    source_label = f" [{args.source}]" if args.source else ""
    print(f"Search{source_label}: {query!r}  ({len(results)} results)\n")
    for r in results:
        score = f"{r.score:.3f}" if r.score is not None else "n/a"
        print(f"[{score}] ({r.source}) {r.id}")
        print(f"  {r.snippet[:240]}")
        print()


if __name__ == "__main__":
    main(sys.argv[1:])

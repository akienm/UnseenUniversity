#!/usr/bin/env python3
"""
debug_session_cli.py — CLI wrapper for debug_session.py (skill invocation)

Usage:
  python3 debug_session_cli.py claim [scope]   → prints handle
  python3 debug_session_cli.py status [handle] → prints JSON status
  python3 debug_session_cli.py release [handle]
  python3 debug_session_cli.py query [handle]  → prints log lines

Skills call this instead of `touch debug_session.flag` / `rm debug_session.flag`.
When mcp__igor__cognition_debug is available, skills will call that instead.
"""

import json
import os
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from devices.igor.cognition.debug_session import claim, query, release, status


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: debug_session_cli.py claim|status|release|query [handle|scope]")
        sys.exit(1)

    cmd = args[0]
    arg = args[1] if len(args) > 1 else None

    if cmd == "claim":
        handle = claim(scope=arg or "session")
        print(handle)
    elif cmd == "status":
        print(json.dumps(status(handle=arg), indent=2))
    elif cmd == "release":
        ok = release(handle=arg)
        print("released" if ok else "no active session")
    elif cmd == "query":
        for line in query(handle=arg):
            print(line)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()

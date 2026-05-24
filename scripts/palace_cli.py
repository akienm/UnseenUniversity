#!/usr/bin/env python3
"""palace_cli.py — Browse and edit adc.palace nodes from the terminal.

Usage:
  palace_cli.py ls [PREFIX] [--limit N] [--json]
  palace_cli.py read <PATH> [--json]
  palace_cli.py search <QUERY> [--tag TAG]... [--limit N] [--json]
  palace_cli.py edit <PATH> [--title TEXT] [--content TEXT] [--node-type TYPE]
                            [--tag TAG]... [--json]
  palace_cli.py delete <PATH> [--yes] [--json]
"""

from __future__ import annotations

import json
import os
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unseen_university.devices.librarian.tools.palace_tools import (
    palace_ls,
    palace_read,
    palace_search,
    palace_write,
    _q,
    _exec,
    _PG_URL,
)


def cmd_ls(args: argparse.Namespace) -> None:
    result = palace_ls(prefix=args.prefix or "", limit=args.limit)
    if args.json:
        print(json.dumps({"result": result}))
    else:
        print(result)


def cmd_read(args: argparse.Namespace) -> None:
    result = palace_read(path=args.path)
    if args.json:
        print(json.dumps({"result": result}))
    else:
        print(result)


def cmd_search(args: argparse.Namespace) -> None:
    result = palace_search(query=args.query, tags=args.tag or None, limit=args.limit)
    if args.json:
        print(json.dumps({"result": result}))
    else:
        print(result)


def cmd_edit(args: argparse.Namespace) -> None:
    # Read existing node first to merge partial updates
    rows = _q(
        "SELECT title, content, node_type, metadata FROM adc.palace WHERE path = %s",
        (args.path,),
    )
    if rows:
        existing = rows[0]
        title = args.title if args.title is not None else existing["title"]
        content = args.content if args.content is not None else existing["content"]
        node_type = (
            args.node_type if args.node_type is not None else existing["node_type"]
        )
        existing_tags = (existing["metadata"] or {}).get("tags", [])
        tags = args.tag if args.tag else existing_tags
    else:
        # New node — all fields required except node_type and tags
        if args.title is None or args.content is None:
            print(
                "Error: --title and --content are required when creating a new node.",
                file=sys.stderr,
            )
            sys.exit(1)
        title = args.title
        content = args.content
        node_type = args.node_type or "doc"
        tags = args.tag or []

    result = palace_write(
        path=args.path,
        title=title,
        content=content,
        node_type=node_type,
        tags=tags,
    )
    if args.json:
        print(json.dumps({"result": result}))
    else:
        print(result)


def cmd_delete(args: argparse.Namespace) -> None:
    # Confirm existence
    rows = _q("SELECT path, title FROM adc.palace WHERE path = %s", (args.path,))
    if not rows:
        msg = f"No node found at path '{args.path}'."
        if args.json:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        sys.exit(1)

    title = rows[0]["title"]

    if not args.yes:
        try:
            answer = input(f"Delete '{args.path}' ({title})? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)
        if answer != "y":
            print("Aborted.")
            sys.exit(1)

    count = _exec("DELETE FROM adc.palace WHERE path = %s", (args.path,))
    result = f"Deleted: {args.path} ({count} row removed)"
    if args.json:
        print(json.dumps({"result": result}))
    else:
        print(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="palace_cli.py",
        description="Browse and edit adc.palace nodes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ls
    ls_p = sub.add_parser("ls", help="List nodes under a prefix (indented tree)")
    ls_p.add_argument(
        "prefix", nargs="?", default="", help="Path prefix (default: all)"
    )
    ls_p.add_argument("--limit", type=int, default=50, metavar="N")
    ls_p.add_argument("--json", action="store_true")

    # read
    read_p = sub.add_parser("read", help="Read a single node by exact path")
    read_p.add_argument("path", help="Exact palace path")
    read_p.add_argument("--json", action="store_true")

    # search
    search_p = sub.add_parser(
        "search", help="Full-text search across titles and content"
    )
    search_p.add_argument("query", help="Search terms")
    search_p.add_argument(
        "--tag", action="append", metavar="TAG", help="Filter by tag (repeatable)"
    )
    search_p.add_argument("--limit", type=int, default=10, metavar="N")
    search_p.add_argument("--json", action="store_true")

    # edit
    edit_p = sub.add_parser("edit", help="Create or update a node (upsert)")
    edit_p.add_argument("path", help="Palace path")
    edit_p.add_argument("--title", metavar="TEXT")
    edit_p.add_argument("--content", metavar="TEXT")
    edit_p.add_argument("--node-type", metavar="TYPE", dest="node_type")
    edit_p.add_argument(
        "--tag", action="append", metavar="TAG", help="Overwrite tag list (repeatable)"
    )
    edit_p.add_argument("--json", action="store_true")

    # delete
    del_p = sub.add_parser(
        "delete", help="Delete a node (requires --yes or interactive prompt)"
    )
    del_p.add_argument("path", help="Exact palace path to delete")
    del_p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    del_p.add_argument("--json", action="store_true")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "ls": cmd_ls,
        "read": cmd_read,
        "search": cmd_search,
        "edit": cmd_edit,
        "delete": cmd_delete,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()

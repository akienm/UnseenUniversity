#!/usr/bin/env python3
"""
audit_add.py — register an audit check into audit_checks.json.

Usage:
  python3 audit_add.py add forever <name> --kind shell|sql \\
      --pattern '<cmd>' [--severity low|med|high] [--description '<text>']
  python3 audit_add.py add next <name> --kind shell|sql \\
      --pattern '<cmd>' [--severity low|med|high] [--description '<text>']

Example:
  python3 audit_add.py add forever basedevice-interface-logging \\
    --kind shell \\
    --pattern 'python3 lab/claudecode/audit_check_interface_logging.py' \\
    --severity med \\
    --description 'BaseDevice methods that cross interfaces must log the crossing.'
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUDIT_CHECKS = _REPO_ROOT / "devlab" / "claudecode" / "audit_checks.json"


def _next_code(checks: dict) -> str:
    """Assign the next AR-NNN code from the existing forever list."""
    codes = []
    for entry in checks.get("forever", []):
        code = entry.get("code", "")
        m = re.match(r"AR-(\d+)", code)
        if m:
            codes.append(int(m.group(1)))
    n = max(codes, default=0) + 1
    return f"AR-{n:03d}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register an audit check in audit_checks.json"
    )
    sub = parser.add_subparsers(dest="cmd")
    add_cmd = sub.add_parser("add", help="Add a new check")
    add_cmd.add_argument("mode", choices=["forever", "next"])
    add_cmd.add_argument("name", help="Check name (kebab-case)")
    add_cmd.add_argument("--kind", required=True, choices=["shell", "sql"])
    add_cmd.add_argument("--pattern", required=True, help="Shell command or SQL query")
    add_cmd.add_argument("--severity", default="med", choices=["low", "med", "high"])
    add_cmd.add_argument("--description", default="")

    args = parser.parse_args()
    if args.cmd != "add":
        parser.print_help()
        return 1

    data = json.loads(_AUDIT_CHECKS.read_text())

    existing_names = {e["name"] for e in data.get("forever", [])}
    existing_names |= {e["name"] for e in data.get("next_sweep", [])}
    if args.name in existing_names:
        print(f"ERROR: check '{args.name}' already registered", file=sys.stderr)
        return 1

    entry: dict = {
        "name": args.name,
        "kind": args.kind,
        "pattern": args.pattern,
        "description": args.description,
        "severity": args.severity,
        "added_by": "claude-code",
        "added_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "ack_until": None,
    }

    if args.mode == "forever":
        entry["code"] = _next_code(data)
        data["forever"].append(entry)
    else:
        data.setdefault("next_sweep", []).append(entry)

    _AUDIT_CHECKS.write_text(json.dumps(data, indent=2) + "\n")
    code_str = f" ({entry.get('code', '')})" if args.mode == "forever" else ""
    print(f"Registered: {args.name}{code_str} → {args.mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

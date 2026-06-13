"""Auditor MCP server — stdio JSON-RPC 2.0.

Wire into Claude Code settings as 'auditor':
    {
      "mcpServers": {
        "auditor": {
          "command": "python",
          "args": ["-m", "devices.auditor.mcp_server"],
          "env": {"UU_HOME_DB_URL": "postgresql://..."}
        }
      }
    }

Exposes run_check, run_all, check_add, check_list, allowlist_add, finding_history.
"""

from __future__ import annotations

import json
import sys

from devices.auditor.device import AuditorDevice

_device = AuditorDevice()

_TOOL_SCHEMAS = [
    {
        "name": "run_check",
        "description": "Run a single named audit check. Returns a list containing one finding dict.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Check name (e.g. 'no-sqlite-imports')",
                }
            },
            "required": ["name"],
        },
    },
    {
        "name": "run_all",
        "description": "Run checks at or above severity_min, optionally filtered by kind. Returns list of finding dicts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "severity_min": {
                    "type": "string",
                    "description": "Minimum severity: 'high', 'med', or 'low'. Default: 'med'",
                    "default": "med",
                },
                "kind": {
                    "type": "string",
                    "description": "Optional: filter to only this check kind (e.g. 'baseline', 'shell', 'sql'). Omit to run all kinds.",
                },
            },
        },
    },
    {
        "name": "check_add",
        "description": "Register a new forever check in the checks file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {
                    "type": "string",
                    "description": "One of: shell, grep, sql, python",
                },
                "pattern": {"type": "string"},
                "severity": {
                    "type": "string",
                    "description": "One of: high, med, low",
                },
                "description": {"type": "string"},
            },
            "required": ["name", "kind", "pattern", "severity", "description"],
        },
    },
    {
        "name": "check_list",
        "description": "List all registered checks (forever + next_sweep).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "allowlist_add",
        "description": "Add a suppression pattern to the audit allowlist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Pattern to suppress"},
                "reason": {"type": "string", "description": "Why this is OK"},
            },
            "required": ["pattern", "reason"],
        },
    },
    {
        "name": "finding_history",
        "description": "Return audit findings from the last N days, newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back. Default: 7",
                    "default": 7,
                }
            },
        },
    },
]


def _dispatch(msg: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "auditor", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": _TOOL_SCHEMAS},
        }

    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        try:
            if name == "run_check":
                result = _device.run_check(name=args["name"])
            elif name == "run_all":
                result = _device.run_all(
                    severity_min=args.get("severity_min", "med"),
                    kind=args.get("kind"),
                )
            elif name == "check_add":
                result = _device.check_add(
                    name=args["name"],
                    kind=args["kind"],
                    pattern=args["pattern"],
                    severity=args["severity"],
                    description=args["description"],
                )
            elif name == "check_list":
                result = _device.check_list()
            elif name == "allowlist_add":
                result = _device.allowlist_add(
                    pattern=args["pattern"], reason=args["reason"]
                )
            elif name == "finding_history":
                result = _device.finding_history(days=args.get("days", 7))
            else:
                result = f"ERROR: unknown tool {name!r}"
        except Exception as exc:
            result = f"ERROR: {exc}"

        text = json.dumps(result, default=str)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": isinstance(result, str) and result.startswith("ERROR"),
            },
        }

    if method == "notifications/initialized":
        return None

    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }
                ),
                flush=True,
            )
            continue
        response = _dispatch(msg)
        if response is not None:
            print(json.dumps(response, default=str), flush=True)


if __name__ == "__main__":
    serve()

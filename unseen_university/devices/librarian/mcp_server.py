"""Librarian MCP server — stdio transport, JSON-RPC 2.0.

Phase 2: full tool inventory ported from igor_mcp.py.

Usage (stdio mode, for Claude Code MCP config):
    python -m unseen_university.devices.librarian.mcp_server

Wire into Claude Code settings:
    {
      "mcpServers": {
        "librarian": {
          "command": "python",
          "args": ["-m", "unseen_university.devices.librarian.mcp_server"]
        }
      }
    }
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request

# ── UC registration ───────────────────────────────────────────────────────────

_UC_PORT = int(os.environ.get("IGOR_UC_PORT", "8082"))
_UC_BASE = os.environ.get("IGOR_UC_BASE", f"http://localhost:{_UC_PORT}")
_registered = False


def _register_with_uc() -> None:
    """POST /api/agents/register — fire-and-forget, once per process."""
    global _registered
    if _registered:
        return
    _registered = True
    try:
        body = json.dumps(
            {
                "agent_id": "librarian",
                "capabilities": ["research", "memory", "palace", "tools"],
            }
        ).encode()
        req = urllib.request.Request(
            f"{_UC_BASE}/api/agents/register",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            resp.read()
    except Exception:
        _registered = False  # allow retry if UC was not reachable


# ── JSON-RPC dispatch ─────────────────────────────────────────────────────────


def _send(msg: dict) -> None:
    print(json.dumps(msg), flush=True)


def _dispatch(msg: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method == "initialize":
        threading.Thread(target=_register_with_uc, daemon=True).start()
        from unseen_university.devices.librarian.nighttime_auditor import (
            start_nighttime_auditor,
        )

        start_nighttime_auditor()
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "librarian", "version": "0.2.0"},
                "capabilities": {"tools": {}},
            },
        }

    if method == "tools/list":
        from unseen_university.devices.librarian import tools as _tools

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": _tools.SCHEMAS},
        }

    if method == "tools/call":
        from unseen_university.devices.librarian import tools as _tools

        params = msg.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        try:
            result = _tools.dispatch(name, args)
        except Exception as exc:
            result = f"ERROR: {exc}"
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": str(result)}],
                "isError": str(result).startswith("ERROR"),
            },
        }

    if method == "notifications/initialized":
        return None  # notification — no response

    # Unknown method
    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


def serve() -> None:
    """Read JSON-RPC from stdin, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            )
            continue
        response = _dispatch(msg)
        if response is not None:
            _send(response)


if __name__ == "__main__":
    serve()

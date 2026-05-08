"""Librarian MCP server — stdio transport, skeleton phase.

Phase 1: lists zero tools. Phase 2 adds tool inventory once T-librarian-mcp-tools lands.

Usage (stdio mode, for Claude Code MCP config):
    python -m agent_datacenter.devices.librarian.mcp_server

Wire into Claude Code settings:
    {
      "mcpServers": {
        "librarian": {
          "command": "python",
          "args": ["-m", "agent_datacenter.devices.librarian.mcp_server"]
        }
      }
    }
"""

from __future__ import annotations

import json
import sys


def _send(msg: dict) -> None:
    print(json.dumps(msg), flush=True)


def _dispatch(msg: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "librarian", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": []},  # phase 2 populates this
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

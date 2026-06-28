"""Workspace MCP server — stdio JSON-RPC 2.0.

Wire into Claude Code settings (or agent config) as 'workspace':
    {
      "mcpServers": {
        "workspace": {
          "command": "python",
          "args": ["-m", "unseen_university.devices.workspace.mcp_server"],
          "env": {"WORKSPACE_ROOT": "/path/to/allowed/root"}
        }
      }
    }

Exposes workspace_read_file, workspace_write_file, workspace_run_bash
for non-CC agents that lack native file tools.
"""

from __future__ import annotations

import json
import sys

from unseen_university.devices.workspace.device import WorkspaceDevice

_device = WorkspaceDevice()

_TOOL_SCHEMAS = [
    {
        "name": "workspace_read_file",
        "description": "Read a file within workspace_root. Returns content as UTF-8 string.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (absolute or relative to workspace_root)",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "workspace_write_file",
        "description": "Write content to a file within workspace_root. Creates parent directories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (absolute or relative to workspace_root)",
                },
                "content": {
                    "type": "string",
                    "description": "UTF-8 text content to write",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "workspace_run_bash",
        "description": "Run a shell command with workspace_root as CWD.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Timeout in seconds. Default: 30",
                    "default": 30,
                },
            },
            "required": ["command"],
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
                "serverInfo": {"name": "workspace", "version": "0.1.0"},
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
            if name == "workspace_read_file":
                result = _device.workspace_read_file(path=args["path"])
            elif name == "workspace_write_file":
                result = _device.workspace_write_file(
                    path=args["path"], content=args["content"]
                )
            elif name == "workspace_run_bash":
                result = _device.workspace_run_bash(
                    command=args["command"],
                    timeout_sec=args.get("timeout_sec", 30),
                )
            else:
                result = f"ERROR: unknown tool {name!r}"
        except Exception as exc:
            result = f"ERROR: {exc}"

        text = json.dumps(result, default=str) if result is not None else "null"
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

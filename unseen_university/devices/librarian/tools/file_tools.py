"""file_tools.py — file_read / file_write MCP tools for Librarian.

Protection model: machine-level. This machine is dedicated to the Igor
project; no path whitelist is applied.
"""

from __future__ import annotations

import json
from pathlib import Path


def file_read(path: str) -> dict:
    """Read a file. Returns {content, size_bytes, path, encoding}."""
    from unseen_university.action_log import append_action

    p = Path(path)
    content = p.read_text(encoding="utf-8", errors="replace")
    size_bytes = p.stat().st_size
    result = {
        "content": content,
        "size_bytes": size_bytes,
        "path": str(p),
        "encoding": "utf-8",
    }
    append_action(
        "librarian",
        "file_read",
        {"path": path},
        f"size={size_bytes}",
    )
    return result


def file_write(
    path: str,
    content: str,
    mode: str = "w",
    mkdir: bool = False,
) -> dict:
    """Write content to a file. Returns {written_bytes, path}."""
    from unseen_university.action_log import append_action

    p = Path(path)
    if mkdir:
        p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, mode, encoding="utf-8") as fh:
        fh.write(content)
    written_bytes = len(content.encode("utf-8"))
    result = {"written_bytes": written_bytes, "path": str(p)}
    append_action(
        "librarian",
        "file_write",
        {"path": path, "mode": mode, "mkdir": mkdir},
        f"written={written_bytes}",
    )
    return result


# ── MCP wiring ────────────────────────────────────────────────────────────────

SCHEMAS: list[dict] = [
    {
        "name": "file_read",
        "description": (
            "Read a file from the filesystem. "
            "Returns {content, size_bytes, path, encoding}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative file path",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "file_write",
        "description": (
            "Write content to a file. "
            "Returns {written_bytes, path}. "
            "Use mkdir=true to create parent directories."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative file path",
                },
                "content": {"type": "string", "description": "Content to write"},
                "mode": {
                    "type": "string",
                    "enum": ["w", "a"],
                    "description": "'w' to overwrite (default), 'a' to append",
                    "default": "w",
                },
                "mkdir": {
                    "type": "boolean",
                    "description": "Create parent directories if missing",
                    "default": False,
                },
            },
            "required": ["path", "content"],
        },
    },
]


def dispatch(name: str, args: dict) -> str | None:
    if name == "file_read":
        return json.dumps(file_read(path=args["path"]))
    if name == "file_write":
        return json.dumps(
            file_write(
                path=args["path"],
                content=args["content"],
                mode=args.get("mode", "w"),
                mkdir=bool(args.get("mkdir", False)),
            )
        )
    return None

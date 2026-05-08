"""
memory_palace.py — Navigable tree of signposts for TheIgors project.

T-memory-palace-schema: Shared between Igor and CC. Each node is an address
(pointer to where information lives), not a copy of the information itself.

Tree structure: paths like 'theigors/igor/cognition' with parent_path
'theigors/igor'. Nodes contain a title, brief content (the signpost), and
pointers (file paths, DB table names, tool names, URLs).

Tools:
  palace_read   — read a node or list children at a path
  palace_write  — create or update a node
  palace_tree   — show the full tree structure (compact)
"""

import json
import logging
import os
from datetime import datetime, timezone

from lab.utility_closet.registry import Tool, registry

log = logging.getLogger(__name__)


def _db():
    from ..memory.db_proxy import make_home_proxy

    return make_home_proxy()


def _now():
    return datetime.now(timezone.utc).isoformat()


def palace_read(path: str = "", **_) -> str:
    """Read a palace node by path, or list children if path is a branch."""
    try:
        db = _db()
        path = path.strip().strip("/")

        if not path:
            # Root: list top-level nodes
            with db() as conn:
                conn.execute(
                    "SELECT path, title FROM memory_palace "
                    "WHERE parent_path IS NULL OR parent_path = '' "
                    "ORDER BY path"
                )
                rows = conn.fetchall()
            if not rows:
                return "Palace is empty. Use palace_write to create nodes."
            lines = ["Memory Palace — top level:"]
            for r in rows:
                lines.append(f"  {r['path']}/  {r['title']}")
            return "\n".join(lines)

        # Try exact match first
        with db() as conn:
            conn.execute(
                "SELECT path, title, content, pointers, updated_at, updated_by "
                "FROM memory_palace WHERE path = %s",
                [path],
            )
            node = conn.fetchone()

        # List children
        with db() as conn:
            conn.execute(
                "SELECT path, title FROM memory_palace "
                "WHERE parent_path = %s ORDER BY path",
                [path],
            )
            children = conn.fetchall()

        if not node and not children:
            return f"No palace node at '{path}'."

        lines = []
        if node:
            lines.append(f"# {node['title']}")
            lines.append(f"Path: {node['path']}")
            if node["content"]:
                lines.append(f"\n{node['content']}")
            ptrs = node["pointers"]
            if isinstance(ptrs, str):
                ptrs = json.loads(ptrs)
            if ptrs:
                lines.append("\nPointers:")
                for p in ptrs:
                    if isinstance(p, dict):
                        lines.append(f"  {p.get('type', '?')}: {p.get('ref', p)}")
                    else:
                        lines.append(f"  {p}")
            if node["updated_at"]:
                lines.append(f"\nUpdated: {node['updated_at']} by {node['updated_by']}")

        if children:
            lines.append("\nChildren:")
            for c in children:
                lines.append(f"  {c['path']}/  {c['title']}")

        return "\n".join(lines)

    except Exception as e:
        log.error("palace_read failed: %s", e)
        return f"Error reading palace: {e}"


def palace_write(
    path: str,
    title: str,
    content: str = "",
    pointers: str = "[]",
    **_,
) -> str:
    """Create or update a palace node. Pointers is a JSON array of references."""
    try:
        db = _db()
        path = path.strip().strip("/")
        if not path:
            return "Error: path is required."

        # Derive parent_path
        parts = path.rsplit("/", 1)
        parent_path = parts[0] if len(parts) > 1 else ""

        # Parse pointers
        if isinstance(pointers, str):
            try:
                ptrs = json.loads(pointers)
            except json.JSONDecodeError:
                ptrs = [pointers] if pointers.strip() else []
        else:
            ptrs = pointers

        now = _now()
        updater = os.getenv("IGOR_INSTANCE_ID", "unknown")

        with db() as conn:
            conn.execute(
                "INSERT INTO memory_palace (path, parent_path, title, content, pointers, updated_at, updated_by) "
                "VALUES (?, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (path) DO UPDATE SET "
                "title = EXCLUDED.title, content = EXCLUDED.content, "
                "pointers = EXCLUDED.pointers, updated_at = EXCLUDED.updated_at, "
                "updated_by = EXCLUDED.updated_by",
                [path, parent_path, title, content, json.dumps(ptrs), now, updater],
            )

        return f"Palace node '{path}' written ({title})."

    except Exception as e:
        log.error("palace_write failed: %s", e)
        return f"Error writing palace: {e}"


def palace_tree(root: str = "", **_) -> str:
    """Show the full palace tree structure in compact form."""
    try:
        db = _db()
        root = root.strip().strip("/")

        with db() as conn:
            if root:
                conn.execute(
                    "SELECT path, title FROM memory_palace "
                    "WHERE path = %s OR path LIKE %s "
                    "ORDER BY path",
                    [root, f"{root}/%"],
                )
            else:
                conn.execute("SELECT path, title FROM memory_palace ORDER BY path")
            rows = conn.fetchall()

        if not rows:
            return "Palace is empty." if not root else f"No nodes under '{root}'."

        lines = [f"Memory Palace{f' — {root}' if root else ''}:"]
        for r in rows:
            path = r["path"]
            depth = path.count("/")
            indent = "  " * depth
            name = path.rsplit("/", 1)[-1]
            lines.append(f"{indent}{name}/  {r['title']}")

        return "\n".join(lines)

    except Exception as e:
        log.error("palace_tree failed: %s", e)
        return f"Error reading palace tree: {e}"


# ── Registration ──────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="palace_read",
        description=(
            "Read a memory palace node by path, or list children. "
            "The palace is a navigable tree of signposts — pointers to where "
            "information lives (files, DB tables, tools). Use '' for root."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Tree path like 'theigors/igor/cognition'. Empty for root.",
                    "default": "",
                },
            },
            "required": [],
        },
        fn=palace_read,
    )
)

registry.register(
    Tool(
        name="palace_write",
        description=(
            "Create or update a memory palace node. A node is a signpost: "
            "title, brief content, and pointers (file paths, DB tables, tools). "
            "Nodes form a tree via path structure (e.g. 'theigors/igor/cognition')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Tree path like 'theigors/igor/cognition'.",
                },
                "title": {
                    "type": "string",
                    "description": "Short title for this node.",
                },
                "content": {
                    "type": "string",
                    "description": "Brief signpost text — what's here and why.",
                    "default": "",
                },
                "pointers": {
                    "type": "string",
                    "description": 'JSON array of references: [{"type": "file", "ref": "path"}, ...]',
                    "default": "[]",
                },
            },
            "required": ["path", "title"],
        },
        fn=palace_write,
    )
)

registry.register(
    Tool(
        name="palace_tree",
        description=(
            "Show the full memory palace tree structure in compact form. "
            "Optional root path to show a subtree only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "root": {
                    "type": "string",
                    "description": "Root path to show subtree from. Empty for full tree.",
                    "default": "",
                },
            },
            "required": [],
        },
        fn=palace_tree,
    )
)

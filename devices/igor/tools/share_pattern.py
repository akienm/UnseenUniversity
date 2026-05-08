"""
share_pattern.py — Cross-Igor pattern sharing tools (T-clan-pattern-sharing).

Two tools expose the export/import infrastructure from engram_export.py as
callable tools Igor can invoke directly:

  export_pattern(root_id)  — walk the subgraph from root_id, return JSON
  import_pattern(json_str) — graft the JSON payload into this instance's graph

Patterns are learned structures (engrams, concept trees), not raw data nodes.
Import skips any node whose id already exists — safe to re-run.
"""

from __future__ import annotations

import logging

from lab.utility_closet.registry import Tool, registry

log = logging.getLogger(__name__)

_EXPORT_LIMIT = 500  # BFS safety cap (already enforced in export_engram)


def _get_cortex():
    from ..memory.cortex import Cortex as _Cortex

    return _Cortex(None)


# ── export_pattern ────────────────────────────────────────────────────────────


def export_pattern(root_id: str, max_depth: int = 10, **_) -> str:
    """Export a pattern subgraph as a portable JSON string.

    Walks from root_id via BFS (children + BRANCHIF targets) up to max_depth
    hops or 500 nodes, collecting interpretive edges. Returns JSON on success,
    error string on failure.
    """
    from ..memory.engram_export import export_engram

    cortex = _get_cortex()
    try:
        template = export_engram(cortex, root_id, max_depth=max_depth)
        if template is None:
            return f"error: root node {root_id!r} not found"
        return template.to_json()
    except Exception as exc:
        log.warning("export_pattern failed: %s", exc)
        return f"error: {exc}"


# ── import_pattern ────────────────────────────────────────────────────────────


def import_pattern(json_str: str, parent_id: str = "CP1", **_) -> str:
    """Import a pattern JSON (from export_pattern) into this instance's graph.

    Grafts nodes under parent_id. Existing nodes are skipped. Returns a
    summary: imported/skipped counts and missing dependency list.
    """
    from ..memory.engram_export import import_engram

    cortex = _get_cortex()
    try:
        result = import_engram(cortex, json_str, parent_id=parent_id)
        if "error" in result:
            return f"error: {result['error']}"
        parts = [
            f"imported={result['imported']}",
            f"skipped={result['skipped']}",
            f"root={result['root_id']}",
        ]
        deps = result.get("dependencies", {})
        missing = deps.get("missing", [])
        if missing:
            parts.append(f"missing_deps={missing}")
        return " | ".join(parts)
    except Exception as exc:
        log.warning("import_pattern failed: %s", exc)
        return f"error: {exc}"


# ── registry ──────────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="export_pattern",
        description=(
            "Export a learned pattern subgraph as portable JSON. "
            "Walks from root_id via BFS (children + engram branch targets). "
            "Returns a JSON string that can be sent to another Igor via import_pattern."
        ),
        parameters={
            "type": "object",
            "properties": {
                "root_id": {
                    "type": "string",
                    "description": "Memory id of the root node to export from",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Max BFS hop depth (default 10)",
                },
            },
            "required": ["root_id"],
        },
        fn=export_pattern,
    )
)

registry.register(
    Tool(
        name="import_pattern",
        description=(
            "Import a pattern JSON (from export_pattern) into this instance's graph. "
            "Grafts nodes under parent_id; skips nodes that already exist. "
            "Returns imported/skipped counts and missing dependency list."
        ),
        parameters={
            "type": "object",
            "properties": {
                "json_str": {
                    "type": "string",
                    "description": "JSON string from export_pattern",
                },
                "parent_id": {
                    "type": "string",
                    "description": "Parent node to graft under (default 'CP1')",
                },
            },
            "required": ["json_str"],
        },
        fn=import_pattern,
    )
)

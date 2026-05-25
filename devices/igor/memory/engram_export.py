"""
engram_export.py — T-engram-portability

Export engrams as self-contained templates with dependency manifests.
An exported engram includes:
  - The subgraph (root + all descendants + interpretive edges)
  - A dependency manifest listing what the engram needs from its host

Calve_subtree is the extraction primitive. This module adds:
  - Dependency scanning (code_refs, memory refs, channels, env vars)
  - Serialization to portable JSON format
  - Import/graft with dependency resolution

Usage:
    from devices.igor.memory.engram_export import export_engram, import_engram

    # Export
    template = export_engram(cortex, "ENGRAM_CODE_INIT")
    json_str = template.to_json()

    # Import into another Igor
    result = import_engram(cortex, json_str)
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class EngramTemplate:
    """Portable engram template with dependency manifest."""

    root_id: str
    nodes: list[dict] = field(default_factory=list)  # serialized Memory dicts
    edges: list[dict] = field(default_factory=list)  # interpretive edges
    dependencies: dict = field(default_factory=dict)  # manifest
    exported_at: str = field(default_factory=lambda: datetime.now().isoformat())
    exported_by: str = ""
    version: str = "1.0"

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(
            {
                "root_id": self.root_id,
                "nodes": self.nodes,
                "edges": self.edges,
                "dependencies": self.dependencies,
                "exported_at": self.exported_at,
                "exported_by": self.exported_by,
                "version": self.version,
            },
            indent=indent,
            default=str,
        )

    @classmethod
    def from_json(cls, json_str: str) -> "EngramTemplate":
        d = json.loads(json_str)
        return cls(
            root_id=d["root_id"],
            nodes=d.get("nodes", []),
            edges=d.get("edges", []),
            dependencies=d.get("dependencies", {}),
            exported_at=d.get("exported_at", ""),
            exported_by=d.get("exported_by", ""),
            version=d.get("version", "1.0"),
        )


def _scan_dependencies(nodes: list[dict]) -> dict:
    """Scan a list of serialized memory nodes for external dependencies.

    Returns a manifest dict:
      tool_refs: code_ref values that need to exist in the tool registry
      memory_refs: node IDs referenced that aren't in this subgraph
      channel_refs: comms:// addresses referenced
      env_vars: IGOR_* env vars referenced in narratives or metadata
    """
    node_ids = {n.get("id", "") for n in nodes}

    tool_refs = set()
    memory_refs = set()
    channel_refs = set()
    env_vars = set()

    for node in nodes:
        meta = node.get("metadata", {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        # Tool references (code_ref)
        code_ref = meta.get("code_ref", "")
        if code_ref:
            tool_refs.add(code_ref)

        # Memory references in payload (BRANCHIF/FORKIF/SPAWNIF targets)
        payload = node.get("payload")
        if payload:
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            cells = payload.get("cells", []) if isinstance(payload, dict) else []
            for cell in cells:
                if not isinstance(cell, list) or len(cell) < 2:
                    continue
                op = cell[0]
                if op in ("BRANCHIF", "FORKIF", "SPAWNIF") and len(cell) >= 3:
                    target = str(cell[2]) if len(cell) > 2 else ""
                    if target and target not in node_ids and not target.startswith("@"):
                        memory_refs.add(target)
                if op == "MCPCALL" and len(cell) >= 2:
                    tool_refs.add(str(cell[1]))

        # Parent/children references outside subgraph
        parent = node.get("parent_id", "")
        if parent and parent not in node_ids:
            memory_refs.add(parent)

        # Channel references
        narrative = node.get("narrative", "")
        for match in re.finditer(r"comms://[\w/\-]+", narrative):
            channel_refs.add(match.group(0))
        meta_str = json.dumps(meta) if isinstance(meta, dict) else str(meta)
        for match in re.finditer(r"comms://[\w/\-]+", meta_str):
            channel_refs.add(match.group(0))

        # Env var references
        for match in re.finditer(r"IGOR_[A-Z_]+", narrative + meta_str):
            env_vars.add(match.group(0))

    return {
        "tool_refs": sorted(tool_refs),
        "memory_refs": sorted(memory_refs),
        "channel_refs": sorted(channel_refs),
        "env_vars": sorted(env_vars),
    }


def export_engram(
    cortex,
    root_id: str,
    exported_by: str = "",
    max_depth: int = 10,
) -> Optional[EngramTemplate]:
    """Export an engram subgraph as a portable template.

    Walks the tree from root_id, collecting all descendant nodes and
    interpretive edges. Scans for dependencies. Returns EngramTemplate
    or None on error.
    """
    try:
        root = cortex.get(root_id)
        if not root:
            log.warning("export_engram: root %s not found", root_id)
            return None

        # Collect subgraph via BFS
        nodes = []
        visited = set()
        queue = [root_id]

        while queue and len(visited) < 500:  # safety cap
            node_id = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)

            node = cortex.get(node_id)
            if not node:
                continue

            # Serialize to dict
            node_dict = {
                "id": node.id,
                "narrative": node.narrative,
                "memory_type": (
                    node.memory_type.value
                    if hasattr(node.memory_type, "value")
                    else str(node.memory_type)
                ),
                "parent_id": node.parent_id,
                "metadata": node.metadata,
                "payload": node.payload,
                "valence": node.valence,
                "arousal": node.arousal,
                "source": node.source,
                "confidence": node.confidence,
            }
            nodes.append(node_dict)

            # Add children to queue
            children = node.children_ids if isinstance(node.children_ids, list) else []
            for child_id in children:
                if child_id not in visited:
                    queue.append(child_id)

            # Follow BRANCHIF/FORKIF targets in payload
            if node.payload:
                payload = node.payload if isinstance(node.payload, dict) else {}
                for cell in payload.get("cells", []):
                    if isinstance(cell, list) and len(cell) >= 3:
                        if cell[0] in ("BRANCHIF", "FORKIF", "SPAWNIF"):
                            target = str(cell[2])
                            if target not in visited and not target.startswith("@"):
                                queue.append(target)

        # Collect interpretive edges between nodes in the subgraph
        edges = []
        try:
            with cortex._conn() as conn:
                node_id_list = list(visited)
                if node_id_list:
                    placeholders = ",".join(["?"] * len(node_id_list))
                    rows = conn.execute(
                        f"SELECT from_id, to_id, direction, weight, layer "
                        f"FROM interpretive_edges "
                        f"WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
                        node_id_list + node_id_list,
                    ).fetchall()
                    for r in rows:
                        edges.append(
                            {
                                "from_id": r[0],
                                "to_id": r[1],
                                "direction": r[2],
                                "weight": r[3],
                                "layer": r[4],
                            }
                        )
        except Exception as exc:
            log.warning("export_engram: edge query failed: %s", exc)

        dependencies = _scan_dependencies(nodes)

        return EngramTemplate(
            root_id=root_id,
            nodes=nodes,
            edges=edges,
            dependencies=dependencies,
            exported_by=exported_by,
        )

    except Exception as exc:
        log.error("export_engram failed: %s", exc)
        return None


def import_engram(
    cortex,
    json_str: str,
    parent_id: str = "CP1",
) -> dict:
    """Import an engram template into the graph.

    Grafts nodes under parent_id. Returns a dict with import results:
    {imported: int, skipped: int, missing_deps: list}
    """
    try:
        template = EngramTemplate.from_json(json_str)
    except Exception as exc:
        return {"error": f"invalid template: {exc}"}

    from .models import Memory, MemoryType

    imported = 0
    skipped = 0

    for node_dict in template.nodes:
        node_id = node_dict.get("id", "")
        if not node_id:
            skipped += 1
            continue

        # Check if already exists
        existing = cortex.get(node_id)
        if existing:
            skipped += 1
            continue

        mt_str = node_dict.get("memory_type", "FACTUAL")
        try:
            mt = MemoryType(mt_str)
        except ValueError:
            mt = MemoryType.FACTUAL

        mem = Memory(
            id=node_id,
            narrative=node_dict.get("narrative", ""),
            memory_type=mt,
            parent_id=node_dict.get("parent_id") or parent_id,
            metadata=node_dict.get("metadata", {}),
            payload=node_dict.get("payload"),
            valence=node_dict.get("valence", 0.0),
            arousal=node_dict.get("arousal", 0.0),
            source="engram_import",
            confidence=node_dict.get("confidence", 1.0),
        )
        cortex.store(mem)
        imported += 1

    return {
        "imported": imported,
        "skipped": skipped,
        "root_id": template.root_id,
        "dependencies": template.dependencies,
    }

"""Proof for T-architecture-intention-points (D-canonical-memory-consolidation).

Architecture intention-points live in devlab/runtime/memory/architecture/ as uniform
JSON: a summary of how a subsystem works + pointers to the files that implement it
(intent -> implementation). Both kinds of memory — dev-artifact and graph-tree — are
covered as small bites. The load-bearing guarantee: every pointer RESOLVES (a hollow
point with invented file paths can't pass). RED before the points exist, GREEN after.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ARCH = _REPO / "devlab" / "runtime" / "memory" / "architecture"


def _load() -> dict:
    points = {}
    for f in _ARCH.glob("*.json"):
        rec = json.loads(f.read_text(encoding="utf-8"))
        key = (rec.get("namespace") or [rec["body"].get("subsystem")])[0]
        points[key] = rec["body"]
    return points


def test_both_memory_kinds_have_small_bite_intention_points():
    pts = _load()
    for kind in ("memory-dev-artifacts", "memory-graph-tree"):
        assert kind in pts, f"missing intention-point for {kind}"
        assert pts[kind].get("summary"), f"{kind} has no summary"
        assert pts[kind].get("implementing_files"), f"{kind} has no implementation pointers"


def test_every_intention_point_pointer_resolves():
    """Proof node (one intention): every implementing_files + reference_docs path in
    every architecture intention-point exists in the repo."""
    pts = _load()
    assert pts, "no architecture intention-points found"
    missing = []
    for name, body in pts.items():
        for field in ("implementing_files", "reference_docs"):
            for rel in body.get(field, []):
                if not (_REPO / rel).exists():
                    missing.append(f"{name}:{field}:{rel}")
    assert missing == [], f"intention-point pointers do not resolve: {missing}"

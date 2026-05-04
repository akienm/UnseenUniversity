"""
inertia_map.py — single source of truth for file inertia tiers.

Previously scope_guard, self_edit, skill_filter, and pe_chain each
maintained their own copy of the same HIGH/MEDIUM/LOW file lists.
This module unifies them (T-scope-guard-dedup-with-self-edit).

Tiers:
  HIGH   (0.90+) — brainstem/, memory/models.py, cognition/reasoners/base.py
  MEDIUM (0.50–0.80) — cognition/, cortex.py, main.py, anthropic.py,
                        prefrontal_cortex.py, thalamus.py
  LOW    (0.30)  — everything else (default)

The table is order-sensitive: more-specific entries appear before broader
ones, and the first match wins. Matching is substring-based so both
relative ("brainstem/foo.py") and absolute paths work without
caller-side normalization.
"""

from __future__ import annotations

import os

# (path_fragment, bucket, float_weight)
_TABLE: list[tuple[str, str, float]] = [
    # HIGH — never touch without explicit approval
    ("brainstem/", "HIGH", 0.95),
    ("memory/models.py", "HIGH", 0.95),
    ("cognition/reasoners/base.py", "HIGH", 0.90),
    # MEDIUM — specific files before broad directory prefixes
    ("cognition/prefrontal_cortex.py", "MEDIUM", 0.75),
    ("cognition/reasoners/anthropic.py", "MEDIUM", 0.70),
    ("memory/cortex.py", "MEDIUM", 0.75),
    ("cognition/thalamus.py", "MEDIUM", 0.50),
    ("cognition/", "MEDIUM", 0.75),
    ("anthropic.py", "MEDIUM", 0.70),
    ("main.py", "MEDIUM", 0.50),
    # LOW — freely improvable
    ("tools/", "LOW", 0.30),
    ("dashboard/", "LOW", 0.30),
]

# Pre-built tuples for callers that need the raw fragment lists.
HIGH_PATHS: tuple[str, ...] = tuple(p for p, b, _ in _TABLE if b == "HIGH")
MED_PATHS: tuple[str, ...] = tuple(p for p, b, _ in _TABLE if b == "MEDIUM")


def _norm(path: str) -> str:
    norm = path.replace("\\", "/")
    home = os.path.expanduser("~").replace("\\", "/")
    if norm.startswith(home):
        norm = norm[len(home) :].lstrip("/")
    return norm


def bucket_of(path: str) -> str:
    """Return 'HIGH', 'MEDIUM', or 'LOW' for a given file path."""
    norm = _norm(path)
    for fragment, bucket, _ in _TABLE:
        if fragment in norm:
            return bucket
    return "LOW"


def weight_of(path: str) -> float:
    """Return float inertia weight (0.0–1.0) for a given file path."""
    norm = _norm(path)
    for fragment, _, weight in _TABLE:
        if fragment in norm:
            return weight
    return 0.30

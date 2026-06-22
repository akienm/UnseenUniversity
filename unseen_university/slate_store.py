"""Canonical slate-path resolver — the one place a slate path is computed.

Daily slates live with the rest of the dev-process memory store, at
``${UU_MEMORY_ROOT:-<repo>/devlab/runtime/memory}/slates/YYYYMMDD.slate.txt``
(T-slate-location-canonical-devlab). This replaces the old scattered
``${IGOR_HOME}/claudecode/`` location: slates are dev-process memory, not
runtime data, so they belong in the repo-tracked store next to tickets,
decisions, and proofs — and are resolved through ONE helper instead of ~25
copy-pasted ``IGOR_HOME / "claudecode"`` constructions.

Mirrors ``ticket_store`` / ``proof_store``: shares the same ``memory_root``
resolver, so ``UU_MEMORY_ROOT`` redirects all three together (tests point it
at a tmp dir).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from unseen_university.memory_root import memory_root


def slates_dir() -> Path:
    """The canonical slates directory (created on demand by writers)."""
    return memory_root() / "slates"


def slate_path(date: str) -> Path:
    """Slate path for a specific date. Accepts ``YYYYMMDD`` or ``YYYY-MM-DD``."""
    stamp = date.replace("-", "")
    return slates_dir() / f"{stamp}.slate.txt"


def today_slate_path() -> Path:
    """Today's dated slate file path."""
    return slates_dir() / f"{datetime.now().strftime('%Y%m%d')}.slate.txt"

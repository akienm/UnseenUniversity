"""Canonical memory-store root resolver — the one place the path is computed.

The filesystem memory store (tickets, decisions, slates, proofs, sessions …)
lives at ``${UU_ROOT}/devlab/runtime/memory``. ``UU_MEMORY_ROOT`` overrides it
(tests point it at a tmp dir). This module exists so ``ticket_store``,
``proof_store``, and ``slate_store`` share ONE definition instead of each
copy-pasting it (they did; this consolidates — D-filesystem-memory-store-2026-06-16).

Kept deliberately minimal — only ``os`` / ``pathlib`` / ``uu_root`` — so it can
never introduce an import cycle for the modules that depend on it.
"""

from __future__ import annotations

import os
from pathlib import Path

from unseen_university._uu_root import uu_root


def memory_root() -> Path:
    """Return the filesystem memory-store root.

    ``UU_MEMORY_ROOT`` (if set) wins; otherwise ``<repo>/devlab/runtime/memory``.
    """
    val = os.environ.get("UU_MEMORY_ROOT")
    if val:
        return Path(val)
    return Path(uu_root()) / "devlab" / "runtime" / "memory"

"""
Backends — skill deploy primitive.

A "deploy" links a managed skill from the master repo (`skills/<name>`) into the
target dir (`~/.claude/skills/<name>`) via a **symlink** — never a copy. Copies
drift: a stale `~/.claude/skills` copy diverged from the repo and broke day-close
(2026-06-25). A link can't drift — it always resolves to the one canonical source
(D-skills-two-products / T-skills-single-source-flip).

The backend is a Protocol so the orchestrator (shim.py) stays platform-neutral.
`os.symlink` covers Linux/macOS directly; Windows supports directory symlinks too
(junction / mklink) — a Windows host, when one exists, gets a backend here without
the orchestrator changing.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol


class DeployBackend(Protocol):
    """Contract for deploying a single skill directory."""

    def deploy_skill(self, src: Path, dst: Path) -> None:
        """Ensure dst is a symlink pointing at src. Idempotent: a dst that is
        already the correct link is a no-op; a real dir (stale copy) or wrong
        link at dst is replaced. dst's parent is created if absent."""
        ...

    def is_available(self) -> bool:
        """True if the backend can run on this host."""
        ...


class SymlinkBackend:
    """Symlink-backed deploy — the single-source mechanism on every platform."""

    def is_available(self) -> bool:
        return hasattr(os, "symlink")

    def deploy_skill(self, src: Path, dst: Path) -> None:
        if not src.exists():
            raise FileNotFoundError(f"source skill dir missing: {src}")
        src = src.resolve()
        # Idempotent: already the correct link → nothing to do.
        if dst.is_symlink() and dst.resolve() == src:
            return
        # Replace whatever is there: a stale copy (real dir) or a wrong link.
        if dst.is_symlink() or dst.exists():
            if dst.is_dir() and not dst.is_symlink():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(src, target_is_directory=True)


def select_backend() -> DeployBackend:
    """Pick the deploy backend for this host. Raises if none works."""
    backend = SymlinkBackend()
    if not backend.is_available():
        raise RuntimeError("SymlinkBackend is not available on this host")
    return backend

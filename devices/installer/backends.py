"""
Backends — per-platform skill deploy primitives.

Each backend exposes the same interface so the installer orchestrator
(shim.py) can pick at runtime without conditionals everywhere. rsync is
the Linux/Mac choice; Windows will get a separate backend (probably
robocopy or a Python copy_tree wrapper) when the first Windows box is
brought up.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Protocol


class DeployBackend(Protocol):
    """Per-platform contract for deploying a single skill directory."""

    def deploy_skill(self, src: Path, dst: Path) -> None:
        """Mirror src directory contents into dst, deleting in-dst files
        that no longer exist in src. dst is created if absent."""
        ...

    def is_available(self) -> bool:
        """True if the backend can run on this host."""
        ...


class RsyncBackend:
    """rsync-backed deploy. Used on Linux + macOS."""

    def is_available(self) -> bool:
        return shutil.which("rsync") is not None

    def deploy_skill(self, src: Path, dst: Path) -> None:
        if not src.exists():
            raise FileNotFoundError(f"source skill dir missing: {src}")
        dst.mkdir(parents=True, exist_ok=True)
        # Trailing slashes matter for rsync: "src/" copies CONTENTS of src
        # into dst (rather than creating dst/src/). --delete removes files
        # in dst that no longer exist in src — only safe because we scope
        # to ONE skill's dir at a time, never the whole skills/ root.
        # --checksum forces content-based comparison: a local edit that
        # happens to match size+mtime should still be overwritten by
        # master, since master is the source of truth for managed skills.
        # Cost is fine — skill files are small markdown.
        cmd = [
            "rsync",
            "-a",
            "--checksum",
            "--delete",
            f"{src}/",
            f"{dst}/",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"rsync failed for {src} -> {dst}: {result.stderr}")


class WindowsBackend:
    """shutil-backed deploy. Used on Windows.

    Uses rmtree + copytree to achieve the same mirror semantics as
    rsync --checksum --delete: master always wins, dst files not in src
    are removed. Safe because we scope to ONE skill dir at a time.
    """

    def is_available(self) -> bool:
        return platform.system() == "Windows"

    def deploy_skill(self, src: Path, dst: Path) -> None:
        if not src.exists():
            raise FileNotFoundError(f"source skill dir missing: {src}")
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def select_backend() -> DeployBackend:
    """Pick the right backend for this host. Raises if none works."""
    if platform.system() == "Windows":
        backend = WindowsBackend()
    else:
        backend = RsyncBackend()
    if not backend.is_available():
        raise RuntimeError(
            f"selected backend {type(backend).__name__} is not available "
            f"on this host"
        )
    return backend

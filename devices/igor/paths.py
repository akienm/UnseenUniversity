"""paths.py — PathManager singleton (D108).

Single source of truth for all ~/.TheIgors/* path references.
Replaces every hardcoded Path.home() / ".TheIgors" in the codebase.

Env vars:
    IGOR_RUNTIME_ROOT  — override the default ~/.TheIgors root
    IGOR_INSTANCE_ID   — override the default instance folder (igor_wild_0001)

Usage:
    from igor.paths import paths

    log_path = paths().logs / "errors.log"
    db_path  = paths().instance / "wild-0001.db"
"""

from __future__ import annotations

import os
import threading
from pathlib import Path


class PathManager:
    """Singleton providing all Igor runtime paths."""

    _instance: PathManager | None = None
    _lock = threading.Lock()

    def __new__(cls) -> PathManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = object.__new__(cls)
                    obj._init()
                    cls._instance = obj
        return cls._instance

    def _init(self) -> None:
        root_env = os.getenv("IGOR_RUNTIME_ROOT")
        if root_env:
            self._runtime = Path(root_env).expanduser().resolve()
        else:
            self._runtime = Path.home() / ".TheIgors"

        self._instance_id = os.getenv("IGOR_INSTANCE_ID", "igor_wild_0001")

    # ── Base ──────────────────────────────────────────────────────────────────

    @property
    def runtime(self) -> Path:
        """~/.TheIgors (or IGOR_RUNTIME_ROOT override)."""
        return self._runtime

    @property
    def instance_id(self) -> str:
        """Instance folder name, e.g. 'igor_wild_0001'."""
        return self._instance_id

    # ── Shared runtime dirs ───────────────────────────────────────────────────

    @property
    def logs(self) -> Path:
        return self._runtime / "local" / "logs"

    @property
    def cache(self) -> Path:
        return self._runtime / "cache"

    @property
    def embeddings_cache(self) -> Path:
        return self._runtime / "cache" / "embeddings"

    @property
    def reasoning_cache(self) -> Path:
        return self._runtime / "cache" / "reasoning"

    @property
    def local(self) -> Path:
        return self._runtime / "local"

    @property
    def machines_json(self) -> Path:
        return self._runtime / "local" / "machines.json"

    @property
    def learn_queue(self) -> Path:
        return self.instance / "learn_queue.json"

    @property
    def drain_pid(self) -> Path:
        return self.instance / "drain_learn_queue.pid"

    @property
    def milieu(self) -> Path:
        return self.instance / "milieu_global.json"

    @property
    def benchmarks(self) -> Path:
        return self._runtime / "benchmarks"

    @property
    def training_corpus(self) -> Path:
        return self._runtime / "training_corpus"

    @property
    def cc_channel(self) -> Path:
        return self._runtime / "cc_channel"

    @property
    def soul(self) -> Path:
        return self._runtime / "SOUL.md"

    @property
    def ssh_key(self) -> Path:
        return self._runtime / "local" / "igor_id_rsa"

    @property
    def claudecode(self) -> Path:
        return self._runtime / "claudecode"

    @property
    def cloud_ok_override(self) -> Path:
        return self._runtime / "cloud_ok_override.json"

    # ── Instance-specific dirs ────────────────────────────────────────────────

    @property
    def instance(self) -> Path:
        """Instance dir, e.g. ~/.TheIgors/igor_wild_0001."""
        return self._runtime / self._instance_id

    @property
    def jobs(self) -> Path:
        return self.instance / "jobs"

    @property
    def arbiter_dir(self) -> Path:
        return self.instance / "arbiter"

    @property
    def inbox(self) -> Path:
        return self.instance / "inbox"

    @property
    def consolidation_checkpoint(self) -> Path:
        return self.instance / "consolidation_checkpoint.json"

    @property
    def blocked_edits_log(self) -> Path:
        return self.instance / "blocked_edits.log"

    @property
    def identity(self) -> Path:
        return self.instance / "IDENTITY.md"

    # ── Named files (word graph) ──────────────────────────────────────────────

    def word_graph(self, name: str = "word_graph") -> Path:
        """Path to a named word graph SQLite DB, e.g. ~/.TheIgors/Igor-wild-0001/word_graph.db."""
        return self.instance / f"{name}.db"

    # ── Ebook library ─────────────────────────────────────────────────────────

    @property
    def ebooks_root(self) -> Path:
        """Root of the AkiensMedia/Ebooks folder.

        Resolution order:
          1. EBOOKS_ROOT env var — explicit per-instance override (always wins)
          2. Windows  (os.name == 'nt'):  ~/OneDrive/AkiensMedia/Ebooks
          3. macOS    (sys.platform == 'darwin'):  ~/OneDrive/AkiensMedia/Ebooks
             (falls back to ~/Library/CloudStorage/OneDrive-Personal/AkiensMedia/Ebooks
              if the primary path doesn't exist — newer macOS OneDrive layout)
          4. Linux: ~/.TheIgors/akien/onedrive/AkiensMedia/Ebooks  (CIFS mount)
        """
        import sys

        override = os.getenv("EBOOKS_ROOT")
        if override:
            return Path(override).expanduser()
        if os.name == "nt":
            return Path.home() / "OneDrive" / "AkiensMedia" / "Ebooks"
        if sys.platform == "darwin":
            primary = Path.home() / "OneDrive" / "AkiensMedia" / "Ebooks"
            if primary.exists():
                return primary
            return (
                Path.home()
                / "Library"
                / "CloudStorage"
                / "OneDrive-Personal"
                / "AkiensMedia"
                / "Ebooks"
            )
        # Linux — OneDrive accessed via CIFS mount under runtime root
        return self._runtime / "akien" / "onedrive" / "AkiensMedia" / "Ebooks"

    @property
    def calibre_library(self) -> Path:
        """Path to Calibre Library folder (overridable via CALIBRE_LIBRARY_PATH)."""
        override = os.getenv("CALIBRE_LIBRARY_PATH")
        if override:
            return Path(override).expanduser()
        return self.ebooks_root / "Calibre Portable" / "Calibre Library"

    @property
    def kindle_dir(self) -> Path:
        """Path to Kindle ebooks folder (overridable via KINDLE_BOOKS_PATH)."""
        override = os.getenv("KINDLE_BOOKS_PATH")
        if override:
            return Path(override).expanduser()
        return self.ebooks_root / "Kindle"

    # ── Source root ───────────────────────────────────────────────────────────

    @property
    def source_root(self) -> Path:
        """Repo root (~/TheIgors). This file lives at wild_igor/igor/paths.py."""
        return Path(__file__).resolve().parent.parent.parent


def paths() -> PathManager:
    """Return the PathManager singleton."""
    return PathManager()

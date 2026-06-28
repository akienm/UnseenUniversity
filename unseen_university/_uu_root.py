"""
_uu_root.py — Canonical UU_ROOT resolver.

Returns the absolute path to the UnseenUniversity repo root. Call order:
  1. UU_ROOT env var (explicit override)
  2. Parent of unseen_university package __file__ (works for pip install -e .)
  3. pip show unseen-university Location field
  4. Current working directory (last resort)

Usage:
    from unseen_university._uu_root import uu_root
    tools = Path(uu_root()) / "devlab" / "claudecode"

D-uu-root-env-var-2026-06-09
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def uu_root() -> str:
    """Return absolute path to the UnseenUniversity repo root."""
    if val := os.environ.get("UU_ROOT", "").strip():
        return str(Path(val).resolve())

    try:
        import unseen_university as _pkg
        candidate = Path(_pkg.__file__).parent.parent.resolve()
        if (candidate / "unseen_university").is_dir():
            return str(candidate)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["pip", "show", "unseen-university"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if line.startswith("Location:"):
                loc = Path(line.split(":", 1)[1].strip()).resolve()
                candidate = loc / "UnseenUniversity"
                if candidate.is_dir():
                    return str(candidate)
                return str(loc)
    except Exception:
        pass

    return str(Path.cwd())


def uu_home() -> str:
    """Return the UnseenUniversity runtime data dir — ``~/.unseen_university``.

    Holds runtime state (logs, flags, device cachedstate, vault) — NOT the
    repo (that's :func:`uu_root`), NOT the database.

    DERIVED, not an env var: ``UU_ROOT`` is the only canonical env var. This
    supersedes the ``IGOR_HOME`` env var — a single-repo-era holdover from
    before the bus / MCP / rack existed (T-uu-eliminate-igor-home-env).

    Read at CALL TIME (no caching) so tests redirect the data dir by
    monkeypatching this function rather than setting an env var, e.g.::

        monkeypatch.setattr("unseen_university._uu_root.uu_home",
                            lambda: str(tmp_path))
    """
    return str(Path.home() / ".unseen_university")


def uu_config_dir() -> Path:
    """Return the canonical bundled-config directory — ``unseen_university/config``.

    Bundled config (``profiles/``, ``policies/``, ``audit_checks/``,
    ``granny.yaml``, ``librarian.yaml``, …) moved INSIDE the package at the
    single-import-root collapse (D-single-package-reorg-2026-06-28). Before
    this resolver every call-site reached it by counting
    ``Path(__file__).resolve().parents[N] / "config"`` — a fragile idiom: the
    correct N differs by the call-site's depth, and a file that moves without
    its ``N`` being re-counted silently resolves to the wrong directory (the
    2026-06-28 config-path regression, fixed in 4f0400e5). Anchor on this
    module instead — ``_uu_root.py`` lives at the package root and does not
    move, so the path is depth-independent and move-proof.

    Returns a :class:`pathlib.Path`; callers compose, e.g.
    ``uu_config_dir() / "profiles" / "base.yaml"``.
    """
    return Path(__file__).resolve().parent / "config"

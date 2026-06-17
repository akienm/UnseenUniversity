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

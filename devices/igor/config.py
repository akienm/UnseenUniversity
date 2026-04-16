"""
config.py — T-config-arch-finish (#448)

Single entry point for reading configuration values with proper
precedence:

  1. System environment variable (highest — always wins)
  2. Config files (igor.switches.cfg, igor.models.cfg, etc.)
  3. Code default (lowest — safe fallback)

Usage:
    from wild_igor.igor.config import get, get_bool, get_int, get_float

    enabled = get_bool("IGOR_TURN_PIPELINE", False)
    timeout = get_float("IGOR_OLLAMA_TIMEOUT_SECS", 30.0)
    model = get("OLLAMA_LOCAL_MODEL", "qwen2.5:7b")

Config files are loaded once at import time and cached. Hot-reload
is handled by env_sync.py (HeartbeatSource detects mtime changes).

File precedence (last file wins within tier 2):
    swarm.cfg → igor.cfg → igor.models.cfg → igor.switches.cfg →
    igor.credentials.cfg → igor.context.*.cfg
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_cfg_cache: dict[str, str] = {}
_loaded = False


def _load_cfg_files() -> None:
    """Load all cfg files into _cfg_cache. Called once at first access."""
    global _cfg_cache, _loaded
    if _loaded:
        return
    _loaded = True

    instance_id = os.getenv("IGOR_INSTANCE_ID", "Igor-wild-0001")
    runtime_root = Path(os.getenv("IGOR_RUNTIME_ROOT", str(Path.home() / ".TheIgors")))
    instance_dir = runtime_root / instance_id

    env_file = instance_dir / ".env"
    if env_file.exists():
        _parse_cfg_file(env_file)

    try:
        from .env_sync import _cfg_files

        for cfg_path in _cfg_files(instance_dir):
            _parse_cfg_file(cfg_path)
    except ImportError:
        pass


def _parse_cfg_file(path: Path) -> None:
    """Parse a KEY=VALUE config file into _cfg_cache."""
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            _cfg_cache[key] = value
    except Exception as exc:
        logger.debug("config: failed to parse %s: %s", path, exc)


def get(key: str, default: str = "") -> str:
    """Read a config value. System env wins, then cfg files, then default."""
    env_val = os.environ.get(key)
    if env_val is not None:
        return env_val
    _load_cfg_files()
    return _cfg_cache.get(key, default)


def get_bool(key: str, default: bool = False) -> bool:
    """Read a boolean config value."""
    val = get(key, "")
    if not val:
        return default
    return val.lower() in ("1", "true", "yes")


def get_int(key: str, default: int = 0) -> int:
    """Read an integer config value."""
    val = get(key, "")
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def get_float(key: str, default: float = 0.0) -> float:
    """Read a float config value."""
    val = get(key, "")
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def reload() -> None:
    """Force reload of cfg files. Called by env_sync on hot-reload."""
    global _loaded
    _loaded = False
    _cfg_cache.clear()
    _load_cfg_files()


def dump() -> dict[str, str]:
    """Return all loaded cfg values (for diagnostics)."""
    _load_cfg_files()
    return dict(_cfg_cache)

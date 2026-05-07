"""
Reasoning cache — file-backed TTL cache for Ollama NE/reasoning calls (cache.2).

key:   sha256(model + prompt)
value: JSON {response_text, ts, max_twm_id, model}
ttl:   12 minutes (720 seconds)
invalidate_on: new TWM observations since cache entry was written
               (current max obs id > stored max_twm_id)

Cache location: ~/.TheIgors/cache/reasoning/<sha256>.json

Primary use: NarrativeEngine._call_ollama() — skip repeated Ollama calls when
TWM content hasn't changed since the last NE run.

T-reasoning-cache-sweep (Pass-2 Area 3): periodic sweep on put() deletes
entries older than 2×TTL and caps directory size (default 5000 files,
env-tunable IGOR_REASONING_CACHE_MAX). Prevents unbounded disk growth
from a cache that never cleaned up its own expired entries.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from ..paths import paths

log = logging.getLogger(__name__)

CACHE_DIR = paths().reasoning_cache
TTL_SECONDS = 720  # 12 minutes
# Sweep runs ~1 in N put() calls; N controls amortized cost vs sweep latency.
_SWEEP_EVERY_N_PUTS = int(os.getenv("IGOR_REASONING_CACHE_SWEEP_EVERY", "100"))
_CACHE_MAX_FILES = int(os.getenv("IGOR_REASONING_CACHE_MAX", "5000"))
_put_counter = 0


def _key(model: str, prompt: str) -> str:
    return hashlib.sha256(f"{model}:{prompt}".encode()).hexdigest()


def _sweep() -> tuple[int, int]:
    """Delete entries older than 2×TTL; then, if still over the cap, delete
    oldest-by-mtime until size is at or below the cap.

    Returns (expired_deleted, cap_deleted). Silent on individual file errors.
    """
    if not CACHE_DIR.exists():
        return 0, 0
    expired = 0
    stale_cutoff = time.time() - 2 * TTL_SECONDS
    files: list[tuple[float, Path]] = []
    for entry in CACHE_DIR.iterdir():
        if not entry.is_file():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < stale_cutoff:
            try:
                entry.unlink()
                expired += 1
            except OSError as e:
                log.debug("_sweep: unlink (expired) failed: %s", e)
            continue
        files.append((mtime, entry))
    # Cap: oldest-first eviction until at-or-below limit
    cap_deleted = 0
    if len(files) > _CACHE_MAX_FILES:
        files.sort(key=lambda p: p[0])  # oldest first
        over = len(files) - _CACHE_MAX_FILES
        for _, path in files[:over]:
            try:
                path.unlink()
                cap_deleted += 1
            except OSError as e:
                log.debug("_sweep: unlink (cap) failed: %s", e)
    return expired, cap_deleted


def get(model: str, prompt: str, current_max_twm_id: int) -> Optional[str]:
    """
    Return cached response text if still valid. Returns None if:
    - No cache entry exists
    - TTL has expired
    - New TWM observations arrived since the entry was written
    Stale/corrupt entries are deleted on detection.
    """
    cache_file = CACHE_DIR / f"{_key(model, prompt)}.json"
    if not cache_file.exists():
        return None

    try:
        entry = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        cache_file.unlink(missing_ok=True)
        return None

    # TTL check
    if time.time() - entry.get("ts", 0) > TTL_SECONDS:
        cache_file.unlink(missing_ok=True)
        return None

    # TWM staleness check — any new obs since this entry was written → stale
    if current_max_twm_id > entry.get("max_twm_id", -1):
        cache_file.unlink(missing_ok=True)
        return None

    return entry.get("response_text")


def put(model: str, prompt: str, response_text: str, max_twm_id: int) -> None:
    """Store a response in the cache. Sweep periodically."""
    global _put_counter
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{_key(model, prompt)}.json"
    cache_file.write_text(
        json.dumps(
            {
                "response_text": response_text,
                "ts": time.time(),
                "max_twm_id": max_twm_id,
                "model": model,
            }
        ),
        encoding="utf-8",
    )
    _put_counter += 1
    if _SWEEP_EVERY_N_PUTS > 0 and _put_counter % _SWEEP_EVERY_N_PUTS == 0:
        try:
            _sweep()
        except Exception as e:
            log.debug("put: _sweep failed: %s", e)

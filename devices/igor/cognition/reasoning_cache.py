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
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

CACHE_DIR   = Path.home() / ".TheIgors" / "cache" / "reasoning"
TTL_SECONDS = 720  # 12 minutes


def _key(model: str, prompt: str) -> str:
    return hashlib.sha256(f"{model}:{prompt}".encode()).hexdigest()


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
    """Store a response in the cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{_key(model, prompt)}.json"
    cache_file.write_text(
        json.dumps({
            "response_text": response_text,
            "ts":            time.time(),
            "max_twm_id":    max_twm_id,
            "model":         model,
        }),
        encoding="utf-8",
    )

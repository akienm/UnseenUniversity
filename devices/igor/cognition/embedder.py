"""
Embedder — nomic-embed-text via Ollama (change.37).

embed(text) → list[float] | None
  - Checks SHA-256 file cache first (shared across machines — same model,
    same symbol space, same vector).
  - Falls back to None gracefully if Ollama is unavailable.

cosine_similarity(a, b) → float

Cache location: ~/.TheIgors/cache/embeddings/<sha256>.json
Key: sha256(model:text)   Value: JSON array of floats
"""

import hashlib
import json
from pathlib import Path
from typing import Optional

EMBED_MODEL = "nomic-embed-text"
CACHE_DIR   = Path.home() / ".TheIgors" / "cache" / "embeddings"


def embed(text: str, model: str = EMBED_MODEL) -> Optional[list[float]]:
    """
    Return embedding vector for text. None if Ollama is unavailable.
    Cache is checked before hitting Ollama; result is written to cache.
    """
    if not text or not text.strip():
        return None

    cache_key  = hashlib.sha256(f"{model}:{text}".encode()).hexdigest()
    cache_file = CACHE_DIR / f"{cache_key}.json"

    # ── Cache hit ─────────────────────────────────────────────────────────────
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cache_file.unlink(missing_ok=True)  # corrupt cache — delete and recompute

    # ── Ollama call ───────────────────────────────────────────────────────────
    try:
        import ollama as _ollama
        response = _ollama.embeddings(model=model, prompt=text)
        vector: list[float] = response["embedding"]
        if not vector:
            return None

        # Write to cache
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(vector), encoding="utf-8")
        return vector

    except Exception:
        return None  # Ollama offline or model missing — fail open


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [–1, 1]. Returns 0.0 on empty or mismatched vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)

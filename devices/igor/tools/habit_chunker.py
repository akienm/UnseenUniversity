"""
habit_chunker.py — Habit chunking: discover repeated action sequences in tails.

Biological pattern: basal ganglia compresses repeated sequences into single chunks.
Igor implementation: scan tails for PROC_ habit co-occurrence sequences; store
observed chunks as PROCEDURAL memories with action_sequence metadata.

D277 gap: T-habit-chunking.

Entry point:
  run_habit_chunking(**_) → str   — registered tool, called by PROC_CHUNK_INSPECTOR

Output memories:
  CHUNK_<8-char-hash>  PROCEDURAL  action_sequence=[A, B, C]  observed_count=N
"""

import hashlib
import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone

from ..paths import paths as _paths

log = logging.getLogger(__name__)

_DB_URL = _paths().home_db_url

_MIN_SEQ_LEN = 3  # minimum chunk length
_MIN_COUNT = 5  # minimum occurrences to form a chunk
_LOOK_BACK = 500  # max recent trails to scan (keeps query fast)


# ── Sequence mining ───────────────────────────────────────────────────────────


def _fetch_habit_sequences(db_url: str, look_back: int = _LOOK_BACK) -> list[list[str]]:
    """
    Query tails for the most recent `look_back` trails that have 2+ PROC_ nodes.
    Returns list of sequences (each sequence = ordered list of PROC_ habit IDs).
    """
    import psycopg2

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT trail_id, array_agg(node_id ORDER BY sequence_pos) AS sequence
        FROM tails
        WHERE node_id LIKE 'PROC_%%'
          AND trail_id IS NOT NULL
        GROUP BY trail_id
        HAVING COUNT(*) >= 2
        ORDER BY MAX(recorded_at) DESC
        LIMIT %s
        """,
        (look_back,),
    )
    rows = cur.fetchall()
    conn.close()
    return [list(seq) for _, seq in rows]


def _find_ngrams(
    sequences: list[list[str]], n: int, min_count: int
) -> list[tuple[tuple[str, ...], int]]:
    """
    Count n-grams across all sequences. Return those with count >= min_count,
    sorted descending by count.
    """
    counts: Counter = Counter()
    for seq in sequences:
        for i in range(len(seq) - n + 1):
            counts[tuple(seq[i : i + n])] += 1
    return [(gram, cnt) for gram, cnt in counts.most_common() if cnt >= min_count]


def _chunk_id(gram: tuple[str, ...]) -> str:
    """Stable ID for a chunk: CHUNK_ + first 8 chars of MD5 of joined habit IDs."""
    digest = hashlib.md5("_".join(gram).encode()).hexdigest()
    return f"CHUNK_{digest[:8].upper()}"


def _chunk_narrative(gram: tuple[str, ...], count: int) -> str:
    labels = list(gram)
    steps = " → ".join(labels)
    return (
        f"I repeatedly do this habit sequence ({count} times observed): {steps}. "
        f"This is a compiled chunk: whenever {labels[0]} fires, {labels[1]} and "
        f"{labels[2] if len(labels) > 2 else 'a follow-up'} typically follow. "
        f"Awareness of this chunk lets me anticipate the full sequence from the first trigger."
    )


# ── Storage ───────────────────────────────────────────────────────────────────


def _upsert_chunk(
    db_url: str, chunk_id: str, narrative: str, gram: tuple[str, ...], count: int
) -> None:
    """Deposit a PROCEDURAL chunk memory via cortex.store (single chokepoint).

    The db_url argument is kept for API compatibility with older callers
    but is no longer used — cortex.store() routes through db_proxy, which
    already knows the DB URL. Converting to cortex.store gives this
    function scrub, credential filtering, test_data stamping, and
    D256 id handling for free.
    """
    from ..memory.cortex import Cortex
    from ..memory.models import Memory, MemoryType

    now = datetime.now(timezone.utc).isoformat()
    metadata = {
        "habit_type": "cognitive",
        "action_sequence": list(gram),
        "observed_count": count,
        "chunk_source": "habit_chunking_inspector",
        "inertia": 0.1,
        "why": (
            f"Auto-compiled chunk from {count} observed instances of sequence "
            f"{' → '.join(gram)}. D277 habit chunking."
        ),
    }

    # Cortex is a singleton-ish — construct once per call. This path runs
    # on a slow cadence (chunking inspector habit) so the construct cost
    # is amortized.
    cortex = Cortex()

    mem = Memory(
        id=chunk_id,
        narrative=narrative,
        memory_type=MemoryType.PROCEDURAL,
        metadata=metadata,
        source="habit_chunker",
        certainty=0.7,
        context_of_encoding=f"habit_chunker scan {now[:10]}",
    )
    cortex.store(mem)


# ── Main entry ────────────────────────────────────────────────────────────────


def run_habit_chunking(**_) -> str:
    """
    Scan recent tails for repeated PROC_ habit sequences.
    Store discovered chunks as PROCEDURAL memories with action_sequence metadata.
    Returns summary string.

    Config (env vars):
      IGOR_CHUNK_MIN_LEN    — min sequence length (default 3)
      IGOR_CHUNK_MIN_COUNT  — min occurrences to form chunk (default 5)
      IGOR_CHUNK_LOOK_BACK  — trails to scan (default 500)
    """
    min_len = int(os.getenv("IGOR_CHUNK_MIN_LEN", str(_MIN_SEQ_LEN)))
    min_count = int(os.getenv("IGOR_CHUNK_MIN_COUNT", str(_MIN_COUNT)))
    look_back = int(os.getenv("IGOR_CHUNK_LOOK_BACK", str(_LOOK_BACK)))

    try:
        sequences = _fetch_habit_sequences(_DB_URL, look_back)
    except Exception as e:
        log.warning("[habit_chunker] DB fetch failed: %s", e)
        return f"[habit_chunker] ERROR: {e}"

    if not sequences:
        return "[habit_chunker] no habit sequences in tails — nothing to chunk"

    grams = _find_ngrams(sequences, min_len, min_count)
    if not grams:
        return (
            f"[habit_chunker] scanned {len(sequences)} trails — "
            f"no {min_len}-gram sequences with count>={min_count}"
        )

    stored = 0
    updated = 0
    errors = []
    for gram, count in grams:
        chunk_id = _chunk_id(gram)
        narrative = _chunk_narrative(gram, count)
        try:
            _upsert_chunk(_DB_URL, chunk_id, narrative, gram, count)
            stored += 1
        except Exception as e:
            log.warning("[habit_chunker] upsert failed for %s: %s", chunk_id, e)
            errors.append(str(e)[:60])

    summary = (
        f"[habit_chunker] scanned {len(sequences)} trails — "
        f"found {len(grams)} chunks, stored {stored}"
    )
    if errors:
        summary += f" ({len(errors)} errors: {errors[0]})"
    log.info(summary)
    return summary


# ── Tool registration ─────────────────────────────────────────────────────────

try:
    from devices.igor.tools.registry import Tool, registry

    registry.register(
        Tool(
            name="run_habit_chunking",
            description=(
                "Scan recent habit activation trails for repeated sequences. "
                "Discovers 3+ habit n-grams appearing 5+ times; stores as PROCEDURAL "
                "CHUNK memories with action_sequence metadata. D277 habit chunking."
            ),
            fn=run_habit_chunking,
            parameters={"type": "object", "properties": {}, "required": []},
        )
    )
except Exception as _reg_err:
    log.warning("[habit_chunker] tool registration failed: %s", _reg_err)

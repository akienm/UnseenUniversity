"""
after_action_reviewer.py — T-after-action-capture: army-style after-action review.

After any high-salience conversation (claude-code design session, Akien exchange,
or any cloud-escalated turn), extract key learnings via Ollama and deposit as
FACTUAL memories. Generalises self_trainer's Q&A capture into a deliberate
"what did we learn?" synthesis pass.

run_after_action_review():
  - Reads recent interaction log for CC turns (CC: prefix) and Akien turns
  - For each uncaptured substantive turn: calls Ollama to extract 1-3 key learnings
  - Falls back to raw Q&A deposit if Ollama unavailable
  - Deposits each learning as FACTUAL with source="after_action_review"
  - Deduplicates via turn_id stored in metadata

Called by PROC_AFTER_ACTION (schedule_interval_sec=7200, every 2h).
Forensic log: ~/.TheIgors/logs/after_action_review.log
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..paths import paths as _paths

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_LOOKBACK_MINUTES = 240  # 4 hours
MAX_DEPOSITS_PER_RUN = 8
MIN_INPUT_LEN = 30
MIN_RESPONSE_LEN = 50

# Authors whose turns qualify for after-action review
_REVIEW_AUTHORS = frozenset({"claude-code", "akien"})

_DB_URL = _paths().home_db_url

_EXTRACT_PROMPT = """\
You are reviewing a conversation exchange to extract key learnings.

EXCHANGE:
User: {user_input}

Igor: {response}

Extract 1-3 concise learnings from this exchange. Focus on:
- Design decisions made
- Architecture insights
- Behaviour fixes or improvements
- New capabilities discussed

Format as bullet points. Be specific. Max 200 words total. If nothing significant was learned, reply: SKIP
"""


def _call_ollama(prompt: str) -> Optional[str]:
    """Call Ollama tier.2 for learning extraction. Returns None on failure."""
    try:
        from ..cognition.inference_ollama import route as _route

        host, model = _route("tier2")
    except Exception:
        from ..cognition.inference_ollama import OLLAMA_HOST, OLLAMA_LOCAL_MODEL

        host = OLLAMA_HOST
        model = OLLAMA_LOCAL_MODEL
    try:
        import urllib.request

        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.2},
            }
        ).encode()
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
        text = data.get("message", {}).get("content", "").strip()
        return text or None
    except Exception as exc:
        logger.warning("after_action_reviewer: Ollama call failed — %s", exc)
        return None


def _extract_cc_turn(log_line: str) -> Optional[dict]:
    """
    Parse an interaction log line and return a dict if it's a CC-authored turn.
    Format: ts|turn_id|thread_id|tier|elapsed|$cost|IN:...|OUT:...
    """
    line = log_line.strip()
    if not line or line.startswith("#"):
        return None
    try:
        parts = line.split("|")
        if len(parts) < 8:
            return None
        ts_str, turn_id = parts[0], parts[1]
        in_part = parts[6]
        out_part = parts[7]
        input_text = in_part[3:] if in_part.startswith("IN:") else ""
        response_text = out_part[4:] if out_part.startswith("OUT:") else ""

        # CC turns: input starts with "CC: "
        if not input_text.startswith("CC: "):
            return None

        # Strip the CC: prefix and routing directive footer
        raw = input_text[4:]
        raw = re.split(r"[\n ]\[Routing directive", raw, 1)[0]
        user_input = raw.strip()

        if len(user_input) < MIN_INPUT_LEN or len(response_text) < MIN_RESPONSE_LEN:
            return None

        return {
            "turn_id": turn_id,
            "ts": datetime.fromisoformat(ts_str),
            "user_input": user_input,
            "response": response_text,
            "author": "claude-code",
        }
    except Exception:
        return None


def _already_captured(conn, turn_id: str) -> bool:
    """Return True if this turn_id has already been deposited."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM memories
        WHERE source = 'after_action_review'
          AND metadata->>'turn_id' = %s
        LIMIT 1
        """,
        (turn_id,),
    )
    return cur.fetchone() is not None


def _deposit_learning(conn, turn_id: str, author: str, learning: str) -> str:
    """Insert a FACTUAL memory for this learning via cortex.store().

    conn is kept in the signature for caller compatibility but is no
    longer used — cortex.store routes writes through db_proxy which
    has its own connection management. Single-chokepoint (DP4) gives
    us scrub, credential filtering, test_data stamping automatically.
    """
    from ..memory.cortex import Cortex
    from ..memory.models import Memory, MemoryType
    from ..memory.node_id import new_node_id

    mem_id = new_node_id()
    metadata = {
        "origin": "after_action_review",
        "learned_from": author,
        "turn_id": turn_id,
        "inertia": 0.3,
    }
    cortex = Cortex(db_path=str(_paths().instance / "wild-0001.db"))
    mem = Memory(
        id=mem_id,
        narrative=learning[:2000],
        memory_type=MemoryType.FACTUAL,
        metadata=metadata,
        source="after_action_review",
        confidence=0.75,
        context_of_encoding=f"after_action_review|learned_from={author}",
    )
    cortex.store(mem)
    return mem_id


def run_after_action_review(
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
    max_deposits: int = MAX_DEPOSITS_PER_RUN,
) -> str:
    """
    Scan recent CC interaction turns, extract key learnings via Ollama,
    deposit as FACTUAL memories. Falls back to raw Q&A if Ollama unavailable.

    Returns a summary string for the scheduler log.
    """
    try:
        from ..cognition.forensic_logger import log_cognition_metric

        log_dir = _paths().logs
    except Exception as exc:
        logger.info(f"ERROR: import failed — {exc}")
        return f"[after_action_review] error: {exc}"

    cutoff = datetime.now() - timedelta(minutes=lookback_minutes)

    # Collect CC turns from interaction logs
    turns: list[dict] = []
    today = datetime.now()
    for delta in range(3):
        d = today - timedelta(days=delta)
        p = log_dir / f"interaction.{d.strftime('%Y%m%d')}.log"
        if not p.exists():
            continue
        try:
            with p.open(encoding="utf-8") as f:
                for line in f:
                    parsed = _extract_cc_turn(line)
                    if parsed and parsed["ts"] >= cutoff:
                        turns.append(parsed)
        except Exception as exc:
            logger.warning("after_action_reviewer: log read failed %s — %s", p, exc)

    if not turns:
        logger.info("no CC turns in window")
        return "[after_action_review] no CC turns in window"

    deposited = 0
    skipped = 0

    try:
        import psycopg2

        conn = psycopg2.connect(_DB_URL)
    except Exception as exc:
        logger.info(f"ERROR: DB connect failed — {exc}")
        return f"[after_action_review] DB error: {exc}"

    try:
        for turn in turns:
            if deposited >= max_deposits:
                break
            if _already_captured(conn, turn["turn_id"]):
                skipped += 1
                continue

            # Try Ollama extraction; fall back to raw Q&A
            prompt = _EXTRACT_PROMPT.format(
                user_input=turn["user_input"][:600],
                response=turn["response"][:600],
            )
            learning = _call_ollama(prompt)

            if learning and learning.strip().upper() == "SKIP":
                logger.info(
                    f"SKIP: turn={turn['turn_id'][:8]} (Ollama: nothing significant)"
                )
                skipped += 1
                continue

            if not learning:
                # Ollama unavailable — fall back to raw Q&A deposit
                learning = f"Q: {turn['user_input'][:400]}\nA: {turn['response'][:400]}"
                logger.info(
                    f"FALLBACK: turn={turn['turn_id'][:8]} (Ollama unavailable)"
                )

            mem_id = _deposit_learning(conn, turn["turn_id"], turn["author"], learning)
            deposited += 1
            logger.info(
                f"DEPOSITED: turn={turn['turn_id'][:8]} author={turn['author']}"
                f" mem={mem_id[:8]} learning_len={len(learning)}"
            )
            try:
                log_cognition_metric(
                    metric="after_action_deposit",
                    value=1.0,
                    detail=f"author={turn['author']} mem={mem_id[:8]}",
                )
            except Exception:
                pass
    finally:
        conn.close()

    summary = f"scanned={len(turns)} deposited={deposited} skipped={skipped}"
    logger.info(f"DONE: {summary}")
    return f"[after_action_review] {summary}"


# ── Register ──────────────────────────────────────────────────────────────────

from .registry import Tool, registry  # noqa: E402

registry.register(
    Tool(
        name="run_after_action_review",
        description=(
            "T-after-action-capture: scan recent CC-authored interaction turns, "
            "extract key learnings via Ollama, deposit as FACTUAL memories. "
            "Falls back to raw Q&A if Ollama unavailable. "
            "Called by PROC_AFTER_ACTION every 2h."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_after_action_review,
    )
)

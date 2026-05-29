"""
purpose_annotator.py — Annotate engrams with purpose, purpose_category.

Called from coa.py _ne_worker after each NE run (batch_size=2 to stay within
budget). retroactive_sweep() can be called manually to process existing engrams.

LLM call: call_inner_cc_long with a compact prompt. Haiku-tier.
Budget gate: skip if <5 engrams to annotate (not worth the LLM call).
"""

from __future__ import annotations
import json
import logging
import os

log = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
You are annotating an Igor memory for inspection purposes.
Memory type: {memory_type}
Memory content: {narrative}

Respond with ONLY valid JSON (no other text):
{{"purpose": "<one sentence: why does this memory exist?>", "category": "<one of: skill, fact, preference, constraint, decision, experience, procedure, observation>"}}
"""


def _annotate_one(narrative: str, memory_type: str) -> dict | None:
    """Get purpose + category for one memory. Fast-path: rule-based classifier; LLM fallback."""
    # Compiled inference fast-path — avoids LLM call for the majority of cases
    try:
        from devices.scraps.purpose_classifier import classify_purpose

        cat, conf = classify_purpose(narrative, memory_type)
        if conf == "HIGH" and cat:
            log.debug("_annotate_one fast-path: %s → %s", memory_type, cat)
            # Purpose sentence is deliberately empty for rule-classified memories;
            # category is the primary signal and carries the value.
            return {"purpose": "", "category": cat}
    except Exception as e:
        log.debug("_annotate_one fast-path failed: %s", e)

    # LLM fallback for ambiguous cases
    try:
        from ..tools.inner_cc import call_inner_cc_long

        prompt = _PROMPT_TEMPLATE.format(
            memory_type=memory_type,
            narrative=(narrative or "")[:600],
        )
        result = call_inner_cc_long(task=prompt, model="anthropic/claude-haiku-4-5")
        raw = (result.get("answer") or "").strip()
        # strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        purpose = str(parsed.get("purpose", "")).strip()
        category = str(parsed.get("category", "")).strip().lower()
        from ..memory.models import PURPOSE_CATEGORIES

        if category not in PURPOSE_CATEGORIES:
            category = ""
        return {"purpose": purpose, "category": category}
    except Exception as e:
        log.debug("_annotate_one failed: %s", e)
        return None


def annotate_pending(cortex, batch_size: int = 2) -> int:
    """
    Find PROCEDURAL/FACTUAL/INTERPRETIVE memories missing purpose, annotate up to batch_size.
    Returns number of memories annotated.
    """
    try:
        import psycopg2

        db_url = os.environ.get("IGOR_HOME_DB_URL", "")
        if not db_url:
            return 0
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(
            """SELECT id, narrative, memory_type FROM clan.memories
               WHERE memory_type IN ('PROCEDURAL', 'FACTUAL', 'INTERPRETIVE')
               AND (metadata->>'purpose' IS NULL OR metadata->>'purpose' = '')
               ORDER BY timestamp DESC
               LIMIT %s""",
            (batch_size,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        log.debug("annotate_pending DB query failed: %s", e)
        return 0

    if not rows:
        return 0

    annotated = 0
    for mem_id, narrative, memory_type in rows:
        result = _annotate_one(narrative or "", memory_type or "")
        if not result:
            continue
        try:
            mem = cortex.get(mem_id)
            if mem is None:
                continue
            mem.set_purpose(result["purpose"], result.get("category", ""))
            cortex.store(mem)
            annotated += 1
            log.info(
                "purpose annotated: %s → %r [%s]",
                mem_id,
                result["purpose"][:60],
                result.get("category"),
            )
        except Exception as e:
            log.debug("annotate_pending store failed for %s: %s", mem_id, e)

    return annotated


def retroactive_sweep(cortex, batch_size: int = 20) -> int:
    """
    Sweep all memories missing purpose across all annotatable types.
    Runs annotate_pending() in chunks. Returns total annotated.
    """
    total = 0
    while True:
        n = annotate_pending(cortex, batch_size=batch_size)
        total += n
        if n < batch_size:
            break
    log.info("retroactive_sweep complete: %d memories annotated", total)
    return total

"""
output_trainer.py — Output training loop: cloud responses → RESPONSE habits.

Reads interaction.YYYYMMDD.log for short-input cloud turns (cost > threshold,
input < MAX_INPUT_LEN, response < MAX_RESPONSE_LEN), extracts trigger keywords,
and seeds RESPONSE habits where no similar habit already exists.

RESPONSE habits are served at tier.1 (no inference) by main.py via
habit.metadata["response_template"]. Each habit seeded here removes one cloud
round-trip for the next matching query.

Complement to self_trainer.py (which deposits FACTUAL knowledge).
self_trainer: what Igor knows → FACTUAL memories.
output_trainer: how Igor responds → RESPONSE habits.

Basket concern keys (D250): none — background batch pass.
Log: ~/.unseen_university/logs/cognition_metrics.log via log_cognition_metric.

Filters vs self_trainer:
  - Shorter inputs (10–80 chars): specific questions → direct canned answers
  - Shorter responses (< 200 chars): long responses need LLM flexibility
  - Skips if similar trigger already exists in RESPONSE habits (dedup)
  - identity_weight bias: identity_weight memories sampled preferentially (future)
"""

from __future__ import annotations

import json
import logging
import os
from ..igor_base import IgorBase
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..paths import paths as _paths

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

CLOUD_COST_THRESHOLD = 0.001  # slightly higher than self_trainer — quality bar
MAX_INPUT_LEN = 80  # short specific questions only
MIN_INPUT_LEN = 10
MAX_RESPONSE_LEN = 200  # long responses need LLM flexibility
MIN_RESPONSE_LEN = 15
MAX_SEEDS_PER_RUN = 5  # conservative — habits are higher-value deposits
DEFAULT_LOOKBACK_MINUTES = 120

TRIGGER_OVERLAP_DEDUP = 3  # skip if existing RESPONSE habit shares >= N tokens

_SKIP_PREFIXES = ("CC:", "[BOOT", "[NE#", "IGOR_", "RESTART", "SCHEDULER_TICK")

# Skip inputs that require real-time state — those belong to inhibition/TWM, not habits
_SKIP_PATTERNS = [
    r"what time",
    r"what.s the time",
    r"current time",
    r"what.s today",
    r"what day",
    r"are you running",
    r"are you (up|alive|there|online)",
    r"^(hi|hey|hello|yo|sup)\b",
]
_SKIP_RE = re.compile("|".join(_SKIP_PATTERNS), re.IGNORECASE)

_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "is",
    "it",
    "be",
    "as",
    "was",
    "are",
    "were",
    "been",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "can",
    "not",
    "no",
    "this",
    "that",
    "i",
    "you",
    "he",
    "she",
    "we",
    "they",
    "what",
    "how",
    "when",
    "where",
    "who",
    "which",
    "if",
    "then",
    "so",
    # Channel metadata tokens — prevent these from becoming trigger keywords
    "talking",
    "relationship",
    "operator",
    "message",
    "akien",
    "thread",
    "context",
    "recent",
    "exchanges",
    "channel",
    "talking",
    "with",
}


# ── Core class ────────────────────────────────────────────────────────────────


class OutputTrainer(IgorBase):
    """
    Scans recent interaction logs for short-input cloud turns and seeds
    RESPONSE habits where the matrix has no existing trigger coverage.

    Each seeded habit fires at tier.1 (no inference) for the next matching query.
    """

    def __init__(self, db_url: str, log_dir: Path):
        self.db_url = db_url
        self.log_dir = Path(log_dir)

    # ── Log reading (shared pattern with SelfTrainer) ─────────────────────────

    def _log_paths(self, lookback_minutes: int) -> list[Path]:
        today = datetime.now()
        paths = []
        for delta_days in range(3):
            d = today - timedelta(days=delta_days)
            p = self.log_dir / f"interaction.{d.strftime('%Y%m%d')}.log"
            if p.exists():
                paths.append(p)
        return paths

    def _parse_interaction_line(self, line: str) -> Optional[dict]:
        """
        Parse: ts|turn_id|thread_id|tier|{elapsed}ms|${cost}|IN:{input}|OUT:{output}
        Returns None if malformed.
        """
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        try:
            parts = line.split("|")
            if len(parts) < 8:
                return None
            ts_str, turn_id, _thread, tier = parts[0], parts[1], parts[2], parts[3]
            cost = float(parts[5].lstrip("$"))
            in_part = parts[6]
            out_part = parts[7]
            ts = datetime.fromisoformat(ts_str)
            input_text = in_part[3:] if in_part.startswith("IN:") else ""
            response_text = out_part[4:] if out_part.startswith("OUT:") else ""
            return {
                "ts": ts,
                "turn_id": turn_id,
                "tier": tier,
                "cost": cost,
                "input_text": input_text,
                "response_text": response_text,
            }
        except Exception:
            return None

    def _read_candidate_turns(self, lookback_minutes: int) -> list[dict]:
        """
        Return turns suitable for RESPONSE habit seeding:
          - cost > CLOUD_COST_THRESHOLD
          - MIN_INPUT_LEN <= len(input) <= MAX_INPUT_LEN
          - len(response) <= MAX_RESPONSE_LEN (short = good canned response candidate)
          - not a real-time-state query (time, status, greetings)
          - not a skip prefix
        """
        cutoff = datetime.now() - timedelta(minutes=lookback_minutes)
        seen: set[str] = set()
        results = []

        for path in self._log_paths(lookback_minutes):
            try:
                with path.open(encoding="utf-8") as f:
                    for raw in f:
                        p = self._parse_interaction_line(raw)
                        if p is None:
                            continue
                        if p["ts"] < cutoff:
                            continue
                        if p["cost"] < CLOUD_COST_THRESHOLD:
                            continue
                        inp = p["input_text"]
                        resp = p["response_text"]
                        if not (MIN_INPUT_LEN <= len(inp) <= MAX_INPUT_LEN):
                            continue
                        if not (MIN_RESPONSE_LEN <= len(resp) <= MAX_RESPONSE_LEN):
                            continue
                        if any(inp.startswith(pfx) for pfx in _SKIP_PREFIXES):
                            continue
                        if _SKIP_RE.search(inp):
                            continue
                        if p["turn_id"] in seen:
                            continue
                        seen.add(p["turn_id"])
                        results.append(p)
            except Exception as exc:
                logger.warning("OutputTrainer: failed reading %s — %s", path, exc)

        return results

    # ── Trigger extraction ────────────────────────────────────────────────────

    @staticmethod
    def _strip_input_prefix(text: str) -> str:
        """
        Strip TALKING WITH / relationship header to get actual user message.

        Formatted inputs look like:
          "TALKING WITH: Akien | relationship: operator\n[Web message from akien]: you are?"
        or "[Thread context...]\nTALKING WITH: ...\n[Web message from akien]: <msg>"

        Without stripping, trigger keywords come from the metadata header
        ('talking', 'akien', 'relationship', 'operator') not the actual message.
        """
        # [Web message from X]: <message>
        m = re.search(r"\[Web message from [^\]]+\]:\s*(.+)", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # [CC: X]: <message>
        m = re.search(r"\[CC:[^\]]*\]:\s*(.+)", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # TALKING WITH: header — take everything after last newline before content
        m = re.search(r"TALKING WITH:.*?(?:\n(.+))", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        return text

    @staticmethod
    def _extract_trigger(input_text: str) -> str:
        """
        Extract top keywords from input as BG trigger string.
        Space-separated, 4-6 tokens, stopwords removed, min 4 chars.
        Strips metadata prefix first so trigger reflects actual message content.
        """
        stripped = OutputTrainer._strip_input_prefix(input_text)
        tokens = re.findall(r"[a-zA-Z]{4,}", stripped.lower())
        meaningful = [t for t in tokens if t not in _STOPWORDS][:6]
        return " ".join(meaningful)

    # ── Dedup ─────────────────────────────────────────────────────────────────

    def _trigger_already_covered(self, conn, trigger: str) -> bool:
        """
        Return True if an existing RESPONSE habit shares >= TRIGGER_OVERLAP_DEDUP
        tokens with the new trigger.
        """
        tokens = set(trigger.split())
        if len(tokens) < 2:
            return True  # too short to seed meaningfully

        threshold = min(len(tokens), TRIGGER_OVERLAP_DEDUP)
        cur = conn.cursor()
        cur.execute("""
            SELECT metadata->>'trigger' FROM memories
            WHERE metadata->>'habit_type' = 'response'
              AND metadata->>'trigger' IS NOT NULL
            """)
        for (existing_trigger,) in cur.fetchall():
            if not existing_trigger:
                continue
            existing_tokens = set(existing_trigger.lower().split())
            overlap = len(tokens & existing_tokens)
            if overlap >= threshold:
                return True
        return False

    # ── Seed ─────────────────────────────────────────────────────────────────

    def _seed_response_habit(
        self,
        conn,
        turn_id: str,
        tier: str,
        input_text: str,
        response_text: str,
        trigger: str,
    ) -> str:
        """
        Insert a RESPONSE habit seeded from a cloud turn via cortex.store.
        Returns the habit memory ID. conn kept for caller compat but unused.
        """
        from ..memory.cortex import Cortex
        from ..memory.models import Memory, MemoryType

        mem_id = f"PROC_RESP_AUTO_{turn_id[:6].upper()}"
        narrative = (
            f"[auto-seeded response habit] "
            f"When asked: {input_text!r}, respond directly. "
            f"Seeded from tier={tier} cloud turn."
        )
        metadata = {
            "habit_type": "response",
            "trigger": trigger,
            "response_template": response_text,
            "why": f"Seeded by output_trainer from {tier} cloud turn — removes cloud round-trip.",
            "provenance": "output_training",
            "turn_id": turn_id,
            "inertia": 0.2,
        }
        cortex = Cortex()
        mem = Memory(
            id=mem_id,
            narrative=narrative,
            memory_type=MemoryType.PROCEDURAL,
            metadata=metadata,
            source="output_training",
            certainty=0.7,
            context_of_encoding=f"output_training|tier={tier}",
        )
        cortex.store(mem)
        return mem_id

    # ── Main pass ─────────────────────────────────────────────────────────────

    def run_output_training_pass(
        self,
        lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
        max_seeds: int = MAX_SEEDS_PER_RUN,
    ) -> dict:
        """
        Scan recent cloud turns, find RESPONSE habit candidates, seed habits.

        Returns stats: {scanned, skipped_length, skipped_realtime, skipped_covered,
                        seeded}.
        """
        from ..cognition.forensic_logger import log_cognition_metric

        turns = self._read_candidate_turns(lookback_minutes)
        stats = {
            "scanned": len(turns),
            "skipped_covered": 0,
            "seeded": 0,
        }

        if not turns:
            log_cognition_metric(
                metric="output_training_pass",
                value=0.0,
                detail="scanned=0 (no candidates in window)",
            )
            return stats

        import psycopg2

        conn = psycopg2.connect(self.db_url)
        try:
            seeded = 0
            for turn in turns:
                if seeded >= max_seeds:
                    break
                trigger = self._extract_trigger(turn["input_text"])
                if not trigger:
                    stats["skipped_covered"] += 1
                    continue
                if self._trigger_already_covered(conn, trigger):
                    stats["skipped_covered"] += 1
                    continue
                mem_id = self._seed_response_habit(
                    conn,
                    turn["turn_id"],
                    turn["tier"],
                    turn["input_text"],
                    turn["response_text"],
                    trigger,
                )
                stats["seeded"] += 1
                seeded += 1
                log_cognition_metric(
                    metric="output_training_seed",
                    value=1.0,
                    detail=(
                        f"tier={turn['tier']} mem={mem_id}"
                        f" trigger={trigger!r}"
                        f" input={turn['input_text'][:40]!r}"
                    ),
                )
        finally:
            conn.close()

        log_cognition_metric(
            metric="output_training_pass",
            value=float(stats["seeded"]),
            detail=(
                f"scanned={stats['scanned']}"
                f" covered={stats['skipped_covered']}"
                f" seeded={stats['seeded']}"
            ),
        )
        return stats


# ── Module-level tool function ────────────────────────────────────────────────


def run_output_training_pass() -> str:
    """Tool entry point — no args, called by SchedulerSource."""
    from ..paths import paths as _igor_paths

    db_url = _paths().home_db_url
    log_dir = _igor_paths().logs
    trainer = OutputTrainer(db_url=db_url, log_dir=log_dir)
    try:
        stats = trainer.run_output_training_pass()
        return (
            f"scanned={stats['scanned']}"
            f" covered={stats['skipped_covered']}"
            f" seeded={stats['seeded']}"
        )
    except Exception as exc:
        logger.error("run_output_training_pass failed: %s", exc)
        return f"error: {exc}"


# ── Tool registration ─────────────────────────────────────────────────────────

from unseen_university.devices.igor.tools.registry import Tool, registry  # noqa: E402

registry.register(
    Tool(
        name="run_output_training_pass",
        description=(
            "Run output training pass: scan recent cloud turns, find short-input "
            "direct-answer patterns, seed RESPONSE habits served at tier.1. "
            "Called by SchedulerSource every N minutes."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_output_training_pass,
    )
)

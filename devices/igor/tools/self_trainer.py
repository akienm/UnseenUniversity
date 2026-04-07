"""
self_trainer.py — Self-training loop: cloud inference → matrix gap → deposit.

Reads interaction.YYYYMMDD.log for turns where LLM inference fired (cost > threshold),
checks matrix coverage for each input, and deposits the LLM response as a FACTUAL
memory wherever the matrix was thin.

Gap signal: no memory narrative contains >= MIN_OVERLAP tokens from the input query.
Deposit: FACTUAL memory, source="self_training", confidence=0.7.

Registered as a tool (run_self_training_pass) so SchedulerSource can fire it via
a PROC habit with schedule_interval_sec.

Basket concern keys (D250): none — this is a background batch pass, no basket thread.
Log: ~/.TheIgors/logs/cognition_metrics.log via log_cognition_metric.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

CLOUD_COST_THRESHOLD = 0.0005  # cost_usd above this → LLM inference fired
OLLAMA_TIERS = frozenset(
    {"tier.2"}
)  # local inference tiers — cost=0 but still train-worthy
GAP_KEYWORD_THRESHOLD = 3  # query tokens that must appear in matrix to count as covered
MIN_INPUT_LEN = 20  # skip trivially short inputs
MIN_RESPONSE_LEN = 30  # skip trivially short responses
MAX_DEPOSITS_PER_RUN = 10  # cap deposits per pass
DEFAULT_LOOKBACK_MINUTES = 120

# Skip inputs that are meta-injections or system noise, not organic queries
# NOTE: "CC:" is intentionally NOT skipped — CC messages contain real semantic content
_SKIP_PREFIXES = ("[BOOT", "[NE#", "IGOR_", "RESTART", "SCHEDULER_TICK")

# Common English stopwords — excluded from keyword overlap check
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
}


# ── Core class ────────────────────────────────────────────────────────────────


class SelfTrainer:
    """
    Scans recent interaction logs, finds LLM-answered turns where the matrix
    had thin coverage, and deposits LLM responses as FACTUAL memories.

    The training signal: every cloud call that the matrix couldn't avoid becomes
    a deposit that may prevent the next identical call.
    """

    def __init__(self, db_url: str, log_dir: Path):
        self.db_url = db_url
        self.log_dir = Path(log_dir)

    # ── Log reading ───────────────────────────────────────────────────────────

    def _log_paths(self, lookback_minutes: int) -> list[Path]:
        """Return interaction log paths covering the lookback window."""
        today = datetime.now()
        paths = []
        for delta_days in range(3):  # today + 2 prior days
            d = today - timedelta(days=delta_days)
            p = self.log_dir / f"interaction.{d.strftime('%Y%m%d')}.log"
            if p.exists():
                paths.append(p)
        return paths

    def _parse_interaction_line(self, line: str) -> Optional[dict]:
        """
        Parse one interaction log line.

        Format (from forensic_logger.log_interaction):
          ts|turn_id|thread_id|tier|{elapsed}ms|${cost}|IN:{input_preview}|OUT:{output_preview}

        Returns dict or None if malformed / too short.
        """
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        try:
            parts = line.split("|")
            if len(parts) < 8:
                return None
            ts_str, turn_id, thread_id, tier = parts[0], parts[1], parts[2], parts[3]
            cost_str = parts[5]  # "$0.00782"
            in_part = parts[6]  # "IN:some text"
            out_part = parts[7]  # "OUT:some text"

            cost = float(cost_str.lstrip("$"))
            ts = datetime.fromisoformat(ts_str)
            input_text = in_part[3:] if in_part.startswith("IN:") else ""
            response_text = out_part[4:] if out_part.startswith("OUT:") else ""

            # Strip synthetic wrappers — extract the actual user query from:
            #   "[Thread context — ...] ... [Web message from X]: <query>"
            #   "CC: <query>\n[Routing directive:...]"
            if "[Web message from" in input_text:
                # Extract everything after the last ]: delimiter
                raw = input_text.split("[Web message from", 1)[1]
                input_text = (
                    raw.split("]:", 1)[-1].strip() if "]:" in raw else input_text
                )
            elif input_text.startswith("CC: "):
                # Strip "CC: " prefix and routing directive footer
                # Note: \n is collapsed to space in log, so match both forms
                raw = input_text[4:]
                raw = re.split(r"[\n ]\[Routing directive", raw, 1)[0]
                input_text = raw.strip()
            elif "[Thread context" in input_text:
                # Unknown thread-context format — skip (can't extract cleanly)
                return None

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
        Return interaction log entries where LLM inference likely fired:
          - cost > CLOUD_COST_THRESHOLD (cloud), OR tier in OLLAMA_TIERS (local, cost=0)
          - input_text doesn't start with a skip prefix
          - input / response have minimum length
          - within lookback window
        """
        cutoff = datetime.now() - timedelta(minutes=lookback_minutes)
        seen_turn_ids: set[str] = set()
        results = []

        for path in self._log_paths(lookback_minutes):
            try:
                with path.open(encoding="utf-8") as f:
                    for raw_line in f:
                        parsed = self._parse_interaction_line(raw_line)
                        if parsed is None:
                            continue
                        if parsed["ts"] < cutoff:
                            continue
                        is_cloud = parsed["cost"] >= CLOUD_COST_THRESHOLD
                        is_ollama = parsed["tier"] in OLLAMA_TIERS
                        if not is_cloud and not is_ollama:
                            continue
                        if len(parsed["input_text"]) < MIN_INPUT_LEN:
                            continue
                        if len(parsed["response_text"]) < MIN_RESPONSE_LEN:
                            continue
                        if any(
                            parsed["input_text"].startswith(p) for p in _SKIP_PREFIXES
                        ):
                            continue
                        if parsed["turn_id"] in seen_turn_ids:
                            continue
                        seen_turn_ids.add(parsed["turn_id"])
                        results.append(parsed)
            except Exception as exc:
                logger.warning("SelfTrainer: failed reading %s — %s", path, exc)

        return results

    def _read_candidate_turns_from_db(
        self, lookback_minutes: int, seen_inputs: set[str]
    ) -> list[dict]:
        """
        Phase 2: read cloud and Ollama turns from EPISODIC memories in Postgres.
        Supplements log-file reading — catches turns where logs are thin or missing.
        Includes: used_api=true (cloud) OR tier_hint in OLLAMA_TIERS (local inference).
        Deduplicates against seen_inputs (content hash) to avoid double-training.
        """
        import psycopg2

        cutoff_iso = (datetime.now() - timedelta(minutes=lookback_minutes)).isoformat()
        ollama_tiers_list = list(OLLAMA_TIERS)
        results = []
        try:
            conn = psycopg2.connect(self.db_url)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT metadata->>'user_input', metadata->>'response',
                           metadata->>'tier_hint', id
                    FROM memories
                    WHERE memory_type = 'EPISODIC'
                      AND (
                        (jsonb_exists(metadata, 'used_api') AND metadata->>'used_api' = 'true')
                        OR metadata->>'tier_hint' = ANY(%s)
                      )
                      AND "timestamp" > %s
                    ORDER BY "timestamp" DESC
                    LIMIT 50
                    """,
                    (ollama_tiers_list, cutoff_iso),
                )
                rows = cur.fetchall()
            conn.close()
        except Exception as exc:
            logger.warning("SelfTrainer: DB read failed — %s", exc)
            return []

        for user_input, response, tier_hint, mem_id in rows:
            if not user_input or not response:
                continue
            # Apply same extraction logic as log parser
            extracted = self._extract_user_query(user_input)
            if extracted is None:
                continue
            if len(extracted) < MIN_INPUT_LEN or len(response) < MIN_RESPONSE_LEN:
                continue
            if any(extracted.startswith(p) for p in _SKIP_PREFIXES):
                continue
            key = extracted[:80]
            if key in seen_inputs:
                continue
            seen_inputs.add(key)
            results.append(
                {
                    "ts": datetime.now(),  # approximate
                    "turn_id": mem_id,
                    "tier": tier_hint or "tier.?",
                    "cost": 0.01,  # unknown; treat as cloud
                    "input_text": extracted,
                    "response_text": response,
                }
            )
        return results

    @staticmethod
    def _extract_user_query(input_text: str) -> Optional[str]:
        """Extract the clean user query from a synthetic input string."""
        if "[Web message from" in input_text:
            raw = input_text.split("[Web message from", 1)[1]
            return raw.split("]:", 1)[-1].strip() if "]:" in raw else None
        if input_text.startswith("CC: "):
            raw = input_text[4:]
            raw = re.split(r"[\n ]\[Routing directive", raw, 1)[0]
            return raw.strip()
        if "[Thread context" in input_text:
            return None
        return input_text.strip()

    # ── Gap detection ─────────────────────────────────────────────────────────

    @staticmethod
    def _query_tokens(text: str) -> list[str]:
        """Return non-stopword tokens longer than 3 chars from text."""
        tokens = re.findall(r"[a-zA-Z]{4,}", text.lower())
        return [t for t in tokens if t not in _STOPWORDS][:12]

    def _matrix_covers(self, conn, query: str) -> bool:
        """
        Return True if the matrix already has a memory whose narrative
        contains at least GAP_KEYWORD_THRESHOLD tokens from the query.

        Phase 1 approximation — no embedding call. Phase 2 cosine runs
        automatically on the next cortex.search() when embeddings are live.
        """
        tokens = self._query_tokens(query)
        if len(tokens) < 2:
            return True  # too short to gap-check meaningfully

        # For each token, check if at least one FACTUAL/INTERPRETIVE memory contains it
        # "covered" = at least GAP_KEYWORD_THRESHOLD tokens found
        covered_count = 0
        cur = conn.cursor()
        for token in tokens:
            cur.execute(
                """
                SELECT 1 FROM memories
                WHERE memory_type IN ('FACTUAL', 'INTERPRETIVE')
                  AND narrative ILIKE %s
                LIMIT 1
                """,
                (f"%{token}%",),
            )
            if cur.fetchone():
                covered_count += 1
                if covered_count >= GAP_KEYWORD_THRESHOLD:
                    return True
        return False

    # ── Deposit ───────────────────────────────────────────────────────────────

    def _deposit(
        self,
        conn,
        turn_id: str,
        tier: str,
        input_text: str,
        response_text: str,
    ) -> str:
        """
        Insert a FACTUAL memory from a cloud LLM response.
        Returns the memory ID. ON CONFLICT DO NOTHING is idempotent.
        """
        from ..memory.node_id import new_node_id

        mem_id = new_node_id()
        narrative = f"Q: {input_text}\nA: {response_text}"
        now = datetime.now().isoformat()
        metadata = json.dumps(
            {
                "origin": "self_training",
                "tier": tier,
                "turn_id": turn_id,
                "inertia": 0.2,
            }
        )
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO memories
                (id, narrative, memory_type, source, confidence,
                 context_of_encoding, timestamp, updated_at,
                 metadata, portable, scope)
            VALUES (%s, %s, 'FACTUAL', 'self_training', 0.7,
                    %s, %s, %s, %s, 1, 'class')
            ON CONFLICT (id) DO NOTHING
            """,
            (
                mem_id,
                narrative,
                f"self_training|tier={tier}",
                now,
                now,
                metadata,
            ),
        )
        conn.commit()
        return mem_id

    # ── Main pass ─────────────────────────────────────────────────────────────

    def run_training_pass(
        self,
        lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
        max_deposits: int = MAX_DEPOSITS_PER_RUN,
    ) -> dict:
        """
        Scan recent cloud turns, find matrix gaps, deposit knowledge.

        Returns stats dict: {scanned, covered, gaps_found, deposited}.
        Logs to cognition_metrics.log.
        """
        from ..cognition.forensic_logger import log_cognition_metric

        # Merge log-file and DB sources; deduplicate by input content
        turns = self._read_candidate_turns(lookback_minutes)
        seen_inputs = {t["input_text"][:80] for t in turns}
        db_turns = self._read_candidate_turns_from_db(lookback_minutes, seen_inputs)
        turns = turns + db_turns
        stats = {
            "scanned": len(turns),
            "covered": 0,
            "gaps_found": 0,
            "deposited": 0,
        }

        if not turns:
            log_cognition_metric(
                metric="self_training_pass",
                value=0.0,
                detail="scanned=0 (no cloud or Ollama turns in window)",
            )
            return stats

        import psycopg2

        conn = psycopg2.connect(self.db_url)
        try:
            deposited = 0
            for turn in turns:
                if deposited >= max_deposits:
                    break
                covered = self._matrix_covers(conn, turn["input_text"])
                if covered:
                    stats["covered"] += 1
                    continue
                stats["gaps_found"] += 1
                if not turn["response_text"]:
                    continue
                mem_id = self._deposit(
                    conn,
                    turn["turn_id"],
                    turn["tier"],
                    turn["input_text"],
                    turn["response_text"],
                )
                stats["deposited"] += 1
                deposited += 1
                log_cognition_metric(
                    metric="self_training_deposit",
                    value=1.0,
                    detail=(
                        f"tier={turn['tier']} mem={mem_id}"
                        f" gap={turn['input_text'][:50]!r}"
                    ),
                )
        finally:
            conn.close()

        log_cognition_metric(
            metric="self_training_pass",
            value=float(stats["deposited"]),
            detail=(
                f"scanned={stats['scanned']}"
                f" covered={stats['covered']}"
                f" gaps={stats['gaps_found']}"
                f" deposited={stats['deposited']}"
            ),
        )
        return stats


# ── Module-level tool function (called by SchedulerSource with no args) ───────


def run_self_training_pass() -> str:
    """
    Tool entry point — no args, called by SchedulerSource.
    Reads DB_URL and log dir from environment/paths.
    """
    from ..paths import paths as _igor_paths

    db_url = os.getenv(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    log_dir = _igor_paths().logs
    trainer = SelfTrainer(db_url=db_url, log_dir=log_dir)
    try:
        stats = trainer.run_training_pass()
        return (
            f"scanned={stats['scanned']}"
            f" covered={stats['covered']}"
            f" gaps={stats['gaps_found']}"
            f" deposited={stats['deposited']}"
        )
    except Exception as exc:
        logger.error("run_self_training_pass failed: %s", exc)
        return f"error: {exc}"


# ── Tool registration ─────────────────────────────────────────────────────────

from .registry import Tool, registry  # noqa: E402

registry.register(
    Tool(
        name="run_self_training_pass",
        description=(
            "Run self-training pass: scan recent cloud inference calls, "
            "find matrix gaps (thin coverage), deposit LLM responses as "
            "FACTUAL memories. Called by SchedulerSource every N minutes."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_self_training_pass,
    )
)

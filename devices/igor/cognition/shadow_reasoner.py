"""
shadow_reasoner — dual-path reasoning execution + divergence corpus.

T-shadow-stream-reasoning (D-preparse-architecture-2026-04-22).

On each reasoning turn, Igor's substrate-driven path (trees + workflow
escalation) produces an output. In parallel, a tutor-LLM path runs the
same situation through a reasoning_context(mode="tutor") call. When the
two outputs diverge, the delta is recorded as a training corpus row for
later off-policy learning (T-divergence-learner, future).

## Ship mode — log-only (Akien 2026-04-22)

The ticket scope includes first-result-wins selection logic, but the
initial rollout is log-only: Igor's output always wins, tutor is
recorded silently. This avoids training-on-moving-target while we're
still tuning Igor's own reasoning path. First-result-wins is
scaffolded in `run_shadow_sync()` for future activation without
module rewrite.

## Integration

Entry point: `record_turn_divergence(query, igor_result, ...)` at the
end of TurnPipeline.run_turn(). Fire-and-forget: the tutor call runs
in a detached daemon thread, comparison and persistence happen when
the tutor call completes. Must-not-raise contract: every exception
caught, no effect on Igor's reply.

## Storage

New Postgres table `instance.reasoning_divergence` — telemetry, NOT
a memory table. Consumed by a future learner; Igor never reads it
during a turn.

## Env gate

IGOR_SHADOW_STREAM_ENABLED (default false — opt-in). Safe default
because tutor path costs a real LLM call per turn; ship dark, flip
on when ready to start collecting corpus.

## Why not use a memory-shape

Per D-no-new-memory-schemas / palace rule `no-new-memory-schemas`:
memories are semantic content Igor retrieves during cognition. A
divergence corpus is training telemetry — Igor never retrieves it,
only a learner job consumes it. Telemetry table is correct here.
"""

from __future__ import annotations

import concurrent.futures as _cf
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .prompt_contexts import Provenance as PCProvenance, reasoning_context
from ..igor_base import IgorBase

log = logging.getLogger(__name__)


# ── Env gate ─────────────────────────────────────────────────────────────────


def _enabled() -> bool:
    return os.getenv("IGOR_SHADOW_STREAM_ENABLED", "false").lower() == "true"


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class ReasonResult:
    """One path's reasoning output. `source` distinguishes igor vs tutor."""

    output: str
    confidence: float
    latency_ms: int
    source: str  # "igor" | "tutor"
    error: Optional[str] = None


@dataclass
class DivergenceRecord:
    """One row in the reasoning_divergence corpus."""

    session_id: Optional[str]
    turn_id: Optional[str]
    input_csb: str
    igor: ReasonResult
    tutor: ReasonResult
    winner: str  # "igor" | "tutor" | "tie" | "log_only"
    diverged: bool
    divergence_reason: str


# ── Schema ───────────────────────────────────────────────────────────────────


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS instance.reasoning_divergence (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ DEFAULT now(),
    session_id      TEXT,
    turn_id         TEXT,
    input_csb       TEXT,
    igor_output     TEXT,
    igor_confidence REAL,
    igor_latency_ms INT,
    tutor_output    TEXT,
    tutor_confidence REAL,
    tutor_latency_ms INT,
    winner          TEXT,
    diverged        BOOLEAN,
    divergence_reason TEXT
)
"""

_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_rdiv_ts ON instance.reasoning_divergence (ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_rdiv_session ON instance.reasoning_divergence (session_id)",
    "CREATE INDEX IF NOT EXISTS idx_rdiv_diverged ON instance.reasoning_divergence (diverged) WHERE diverged = true",
]


# ── ShadowReasoner ───────────────────────────────────────────────────────────


class ShadowReasoner(IgorBase):
    """Dual-path reasoning dispatcher with divergence recording.

    Instantiate once at boot with a cortex + inference_gateway reference.
    Register the instance via `set_default_shadow()` so callsites can use
    the `record_turn_divergence()` convenience.
    """

    def __init__(
        self,
        cortex: Any,
        gateway: Any = None,
        milieu: Any = None,
        identity: Any = None,
        tutor_timeout_sec: float = 10.0,
    ) -> None:
        super().__init__()
        self.cortex = cortex
        self.gateway = gateway
        self._milieu = milieu
        self._identity = identity
        self.tutor_timeout_sec = tutor_timeout_sec
        self._schema_ready = False

    def _ensure_schema(self) -> None:
        if self._schema_ready or self.cortex is None:
            return
        try:
            with self.cortex._db() as conn:
                conn.execute(_SCHEMA_SQL)
                for idx_sql in _INDEX_SQL:
                    conn.execute(idx_sql)
            self._schema_ready = True
        except Exception as exc:
            log.debug("shadow_reasoner schema init failed: %s", exc)

    def _run_tutor(self, situation_query: str) -> ReasonResult:
        """Fire one tutor-mode LLM reasoning call."""
        t0 = time.monotonic()
        if self.gateway is None:
            return ReasonResult(
                output="",
                confidence=0.0,
                latency_ms=0,
                source="tutor",
                error="no_gateway",
            )
        try:
            prov = PCProvenance(
                caller="shadow_reasoner",
                situation_source="shadow_stream",
            )
            _ = reasoning_context(
                situation={"query": situation_query[:1000], "context": {}},
                provenance=prov,
                milieu=self._milieu,
                identity=self._identity,
                mode="tutor",
            )
            response_text, _cost, _used_api = self.gateway.reason(
                user_input=situation_query,
                relevant=[],
                core=[],
                level=3,
                cortex=self.cortex,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            return ReasonResult(
                output=response_text or "",
                confidence=0.7,
                latency_ms=latency_ms,
                source="tutor",
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            return ReasonResult(
                output="",
                confidence=0.0,
                latency_ms=latency_ms,
                source="tutor",
                error=f"{type(exc).__name__}: {exc}",
            )

    def fire_and_forget(
        self,
        situation_query: str,
        igor_result: ReasonResult,
        session_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> Optional[threading.Thread]:
        """Primary log-only entry. Non-blocking: spawns a daemon thread
        that runs the tutor path, compares, and persists. Returns the
        thread handle (for tests to join on); None when disabled."""
        if not _enabled():
            return None

        def _run() -> None:
            try:
                self._ensure_schema()
                tutor_result = self._run_tutor(situation_query)
                diverged, reason = self._compare(igor_result, tutor_result)
                record = DivergenceRecord(
                    session_id=session_id,
                    turn_id=turn_id,
                    input_csb=situation_query[:2000],
                    igor=igor_result,
                    tutor=tutor_result,
                    winner="log_only",
                    diverged=diverged,
                    divergence_reason=reason,
                )
                self._persist(record)
            except Exception as exc:
                log.debug("shadow fire_and_forget failed: %s", exc)

        t = threading.Thread(target=_run, daemon=True, name="shadow-reasoner")
        t.start()
        return t

    def run_shadow_sync(
        self,
        situation_query: str,
        igor_fn: Callable[[], ReasonResult],
        confidence_threshold: float = 0.7,
    ) -> ReasonResult:
        """First-confident-wins — SCAFFOLDED for future active-mode activation.

        Runs igor_fn and tutor path in parallel. Returns the first result
        whose confidence clears the threshold, or the faster result if
        neither does. NOT used in log-only ship mode — reserved for when
        the divergence corpus has trained the confidence calibration.
        """
        with _cf.ThreadPoolExecutor(max_workers=2) as pool:
            f_igor = pool.submit(igor_fn)
            f_tutor = pool.submit(self._run_tutor, situation_query)

            first: Optional[ReasonResult] = None
            for fut in _cf.as_completed(
                [f_igor, f_tutor], timeout=self.tutor_timeout_sec
            ):
                try:
                    result = fut.result()
                except Exception as exc:
                    log.debug("shadow_sync path raised: %s", exc)
                    continue
                if result.error:
                    continue
                if first is None:
                    first = result
                if result.confidence >= confidence_threshold:
                    return result
            if first is not None:
                return first
            raise RuntimeError("both reasoning paths failed or returned errors")

    @staticmethod
    def _compare(igor: ReasonResult, tutor: ReasonResult) -> tuple[bool, str]:
        """Semantic-ish comparison. Log-only MVP uses lexical Jaccard
        overlap; a future ticket can upgrade to embedding cosine.

        Returns (diverged, reason). `reason` is the human-readable
        signal stored in the corpus for later analysis.
        """
        if igor.error and tutor.error:
            return True, f"both_errors: igor={igor.error} tutor={tutor.error}"
        if igor.error:
            return True, f"igor_error: {igor.error}"
        if tutor.error:
            return True, f"tutor_error: {tutor.error}"
        a = (igor.output or "").lower().strip()
        b = (tutor.output or "").lower().strip()
        if not a and not b:
            return False, "both_empty"
        if not a or not b:
            return True, "one_empty"
        a_words = set(a.split())
        b_words = set(b.split())
        if not a_words or not b_words:
            return True, "no_word_tokens"
        overlap = len(a_words & b_words) / max(1, len(a_words | b_words))
        if overlap < 0.3:
            return True, f"jaccard={overlap:.2f}"
        return False, f"jaccard={overlap:.2f}"

    def _persist(self, rec: DivergenceRecord) -> None:
        """Write one divergence record. Silent on error — this path must
        never raise into a caller."""
        if self.cortex is None:
            return
        try:
            with self.cortex._db() as conn:
                conn.execute(
                    """INSERT INTO instance.reasoning_divergence
                       (session_id, turn_id, input_csb,
                        igor_output, igor_confidence, igor_latency_ms,
                        tutor_output, tutor_confidence, tutor_latency_ms,
                        winner, diverged, divergence_reason)
                       VALUES (%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s)""",
                    (
                        rec.session_id,
                        rec.turn_id,
                        rec.input_csb,
                        (rec.igor.output or "")[:4000],
                        rec.igor.confidence,
                        rec.igor.latency_ms,
                        (rec.tutor.output or "")[:4000],
                        rec.tutor.confidence,
                        rec.tutor.latency_ms,
                        rec.winner,
                        rec.diverged,
                        rec.divergence_reason,
                    ),
                )
        except Exception as exc:
            log.debug("shadow persist failed: %s", exc)


# ── Module-level default singleton ───────────────────────────────────────────


_default_shadow: Optional[ShadowReasoner] = None


def set_default_shadow(s: Optional[ShadowReasoner]) -> None:
    """Register the process-wide shadow reasoner. main.py wires this at
    boot once cortex + gateway exist. Tests can inject a stub."""
    global _default_shadow
    _default_shadow = s


def default_shadow() -> Optional[ShadowReasoner]:
    return _default_shadow


def record_turn_divergence(
    situation_query: str,
    igor_result: ReasonResult,
    session_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Optional[threading.Thread]:
    """Convenience: route to the default ShadowReasoner's fire_and_forget.
    No-op when the default isn't set or the env gate is off. Safe for
    integration at any reasoning callsite — never raises."""
    try:
        s = default_shadow()
        if s is None:
            return None
        return s.fire_and_forget(
            situation_query=situation_query,
            igor_result=igor_result,
            session_id=session_id,
            turn_id=turn_id,
        )
    except Exception as exc:
        log.debug("record_turn_divergence failed: %s", exc)
        return None

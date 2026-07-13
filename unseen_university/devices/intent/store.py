"""
IntentStore — devlab.predictions + devlab.validations persistence layer.

Tables are created idempotently on first use (no separate migration required,
though m_devlab_intent.py can be run independently to pre-create them).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_DB_URL_KEYS = ("UU_HOME_DB_URL", "UU_HOME_DB_URL")

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS devlab;"

# ``provenance_class`` records WHY the row holds what it holds. It is set from the
# WRITE PATH and the caller cannot lie about it: the except-block writes 'error',
# and it has no other option.
#
#   model  — an inference ran, parsed, and answered. `predicted_intent` is its answer,
#            INCLUDING an honest "unknown" (a refusal is a real answer — CP1).
#   error  — nothing answered. `predicted_intent` is a PLACEHOLDER, not a prediction,
#            and `error_detail` says what went wrong.
#
# WHY THIS COLUMN EXISTS: without it, a crash and an honest refusal wrote the BYTE-
# IDENTICAL row (`unknown`), and 2,435 of 2,504 rows were crashes wearing CP1's
# clothes — a failure impersonating a virtue. An error may collapse into a success
# shape at an INTERFACE (it can be re-called); it may NEVER collapse into a RECORD OF
# TRUTH (it cannot be un-written). See R-feedback-is-unconditional-silence-is-never-
# success and T-intent-extractor-crash-masquerades-as-refusal.
#
# NOTE the name is shared with the intention envelope's `provenance_class`
# ({declared, extracted-upstream, ...}) but the VOCABULARY here is its own — a
# prediction record, not an assertion. Do not conflate the two value sets.
_CREATE_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS devlab.predictions (
    id              UUID PRIMARY KEY,
    context         TEXT NOT NULL,
    domain          TEXT NOT NULL,
    predicted_intent TEXT NOT NULL,
    confidence      FLOAT NOT NULL,
    provenance_class TEXT NOT NULL DEFAULT 'model',
    error_detail    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# The table predates the two columns — add them in place for existing installs.
_MIGRATE_PREDICTIONS = [
    "ALTER TABLE devlab.predictions ADD COLUMN IF NOT EXISTS "
    "provenance_class TEXT NOT NULL DEFAULT 'model';",
    "ALTER TABLE devlab.predictions ADD COLUMN IF NOT EXISTS error_detail TEXT;",
]

_CREATE_VALIDATIONS = """
CREATE TABLE IF NOT EXISTS devlab.validations (
    id              UUID PRIMARY KEY,
    prediction_id   UUID REFERENCES devlab.predictions(id),
    actual_outcome  TEXT NOT NULL,
    match           BOOLEAN,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS intent_predictions_domain ON devlab.predictions (domain);",
    "CREATE INDEX IF NOT EXISTS intent_predictions_created ON devlab.predictions (created_at DESC);",
    "CREATE INDEX IF NOT EXISTS intent_validations_pred_id ON devlab.validations (prediction_id);",
    "CREATE INDEX IF NOT EXISTS intent_validations_domain ON devlab.validations (created_at DESC);",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IntentStore:
    """Low-level CRUD for devlab.predictions and devlab.validations."""

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url
        self._tables_ensured = False

    def _get_db_url(self) -> str:
        if self._db_url:
            return self._db_url
        for key in _DB_URL_KEYS:
            val = os.environ.get(key, "")
            if val:
                return val
        raise RuntimeError(
            "No DB URL found — set UU_HOME_DB_URL or UU_HOME_DB_URL"
        )

    def _connect(self):
        import psycopg2
        return psycopg2.connect(self._get_db_url())

    def ensure_tables(self) -> None:
        if self._tables_ensured:
            return
        try:
            conn = self._connect()
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    cur.execute(_CREATE_SCHEMA)
                    cur.execute(_CREATE_PREDICTIONS)
                    cur.execute(_CREATE_VALIDATIONS)
                    for stmt in _MIGRATE_PREDICTIONS:
                        cur.execute(stmt)
                    for idx in _CREATE_INDEXES:
                        cur.execute(idx)
            finally:
                conn.close()
            self._tables_ensured = True
            log.info("IntentStore: tables ensured")
        except Exception as exc:
            log.warning("IntentStore: ensure_tables failed: %s", exc)

    def save_prediction(
        self,
        context: str,
        domain: str,
        predicted_intent: str,
        confidence: float,
        provenance_class: str = "model",
        error_detail: str | None = None,
    ) -> str:
        """Insert a prediction row; returns UUID string.

        ``provenance_class`` is NOT optional in spirit — it is defaulted only so the
        column has a value for callers that genuinely ran a model. A failing caller
        MUST pass ``'error'`` with the exception text; see the schema comment.
        """
        self.ensure_tables()
        pid = str(uuid.uuid4())
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO devlab.predictions
                        (id, context, domain, predicted_intent, confidence,
                         provenance_class, error_detail, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    """,
                    (pid, context, domain, predicted_intent, confidence,
                     provenance_class, error_detail),
                )
            conn.commit()
        finally:
            conn.close()
        log.info(
            "IntentStore: saved prediction id=%s domain=%s intent=%s class=%s%s",
            pid, domain, predicted_intent, provenance_class,
            f" error={error_detail}" if error_detail else "",
        )
        return pid

    def output_distribution(self, domain: str, window: int = 100) -> dict:
        """Shape of the last ``window`` predictions for a domain.

        Returns ``{samples, distinct, top_value, top_share}``. This is the ONLY
        signal that catches a perfectly-shaped lie: a record-level check cannot
        tell a degenerate device from an honest one, because every individual
        record is well-formed. The DISTRIBUTION is what screams.
        """
        self.ensure_tables()
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT predicted_intent, COUNT(*) AS n
                        FROM (
                            SELECT predicted_intent
                            FROM devlab.predictions
                            WHERE domain = %s
                            ORDER BY created_at DESC
                            LIMIT %s
                        ) recent
                        GROUP BY predicted_intent
                        ORDER BY n DESC
                        """,
                        (domain, window),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("IntentStore: output_distribution failed: %s", exc)
            return {"samples": 0, "distinct": 0, "top_value": None, "top_share": 0.0}

        samples = sum(int(r[1]) for r in rows)
        if not samples:
            return {"samples": 0, "distinct": 0, "top_value": None, "top_share": 0.0}
        return {
            "samples": samples,
            "distinct": len(rows),
            "top_value": rows[0][0],
            "top_share": float(rows[0][1]) / samples,
        }

    def annotate_crash_records(self) -> int:
        """Backfill: mark the historical exception-fallback rows as ``error``.

        The pre-fix except-block wrote ``intent='unknown', confidence=0.0`` — that
        pair IS the crash signature, and no genuine model answer produces it (a real
        refusal carries a real confidence). Those rows are NEVER DELETED (CP2: they
        are the evidence, and 2,435 of them are why we found this at all) — they are
        annotated, so the corpus stops lying about its own health.

        Idempotent: rows already classed ``error`` are skipped. Returns rows updated.
        """
        self.ensure_tables()
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE devlab.predictions
                           SET provenance_class = 'error',
                               error_detail = 'backfilled: pre-fix except-block fallback '
                                              '(cause unrecoverable — the record did not '
                                              'carry it). T-intent-extractor-crash-'
                                              'masquerades-as-refusal'
                         WHERE predicted_intent = 'unknown'
                           AND confidence < 0.005
                           AND provenance_class <> 'error'
                        """
                    )
                    n = cur.rowcount
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("IntentStore: annotate_crash_records failed: %s", exc)
            return 0
        log.info("IntentStore: annotated %d historical crash records as provenance_class=error", n)
        return n

    def save_validation(
        self,
        actual_outcome: str,
        prediction_id: str | None = None,
        match: bool | None = None,
    ) -> str:
        """Insert a validation row; prediction_id may be None (post-hoc path)."""
        self.ensure_tables()
        vid = str(uuid.uuid4())
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO devlab.validations
                        (id, prediction_id, actual_outcome, match, created_at)
                    VALUES (%s, %s, %s, %s, now())
                    """,
                    (vid, prediction_id, actual_outcome, match),
                )
            conn.commit()
        finally:
            conn.close()
        log.info(
            "IntentStore: saved validation id=%s pred=%s match=%s",
            vid, prediction_id, match,
        )
        return vid

    def get_few_shot_examples(self, domain: str, limit: int = 10) -> list[dict]:
        """Return up to `limit` validated (context, outcome) pairs for the domain.

        Joins predictions → validations on prediction_id, filtered by domain,
        newest first. Used to build few-shot context for predict().

        DELIBERATELY DOES NOT FILTER OUT ``provenance_class='error'`` ROWS, and that
        is not an oversight. The training pair is ``(p.context -> v.actual_outcome)``:
        the context is the ticket description and the outcome is the HUMAN-declared
        intention. Neither side is the crashed prediction — ``predicted_intent`` is
        never read here. So the ~2,435 crash rows still carry ~2,435 PERFECTLY GOOD
        training pairs, and excluding them would throw away the entire corpus in the
        name of cleaning it. The crash poisoned the answer, not the question.
        """
        self.ensure_tables()
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT p.context, v.actual_outcome
                        FROM devlab.validations v
                        JOIN devlab.predictions p ON p.id = v.prediction_id
                        WHERE p.domain = %s
                        ORDER BY v.created_at DESC
                        LIMIT %s
                        """,
                        (domain, limit),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("IntentStore: get_few_shot_examples failed: %s", exc)
            return []
        return [{"context": r[0], "outcome": r[1]} for r in rows]

    def get_patterns(self, domain: str) -> list[dict]:
        """Aggregate actual_outcome counts for the domain from validations."""
        self.ensure_tables()
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT v.actual_outcome,
                               COUNT(*) AS validation_count,
                               AVG(CASE WHEN v.match THEN 1.0 ELSE 0.0 END) AS confidence
                        FROM devlab.validations v
                        JOIN devlab.predictions p ON p.id = v.prediction_id
                        WHERE p.domain = %s
                        GROUP BY v.actual_outcome
                        ORDER BY validation_count DESC
                        """,
                        (domain,),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("IntentStore: get_patterns failed: %s", exc)
            return []
        return [
            {
                "pattern": r[0],
                "validation_count": r[1],
                "confidence": round(float(r[2]) if r[2] is not None else 0.0, 4),
            }
            for r in rows
        ]

    def count_validations(self, domain: str) -> int:
        """Return count of validations with a linked prediction in this domain."""
        self.ensure_tables()
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM devlab.validations v
                        JOIN devlab.predictions p ON p.id = v.prediction_id
                        WHERE p.domain = %s
                        """,
                        (domain,),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
            return int(row[0]) if row else 0
        except Exception as exc:
            log.warning("IntentStore: count_validations failed: %s", exc)
            return 0

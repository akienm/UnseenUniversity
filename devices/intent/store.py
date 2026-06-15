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

_DB_URL_KEYS = ("UU_HOME_DB_URL", "IGOR_HOME_DB_URL")

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS devlab;"

_CREATE_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS devlab.predictions (
    id              UUID PRIMARY KEY,
    context         TEXT NOT NULL,
    domain          TEXT NOT NULL,
    predicted_intent TEXT NOT NULL,
    confidence      FLOAT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

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
            "No DB URL found — set UU_HOME_DB_URL or IGOR_HOME_DB_URL"
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
    ) -> str:
        """Insert a prediction row; returns UUID string."""
        self.ensure_tables()
        pid = str(uuid.uuid4())
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO devlab.predictions
                        (id, context, domain, predicted_intent, confidence, created_at)
                    VALUES (%s, %s, %s, %s, %s, now())
                    """,
                    (pid, context, domain, predicted_intent, confidence),
                )
            conn.commit()
        finally:
            conn.close()
        log.info("IntentStore: saved prediction id=%s domain=%s intent=%s", pid, domain, predicted_intent)
        return pid

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

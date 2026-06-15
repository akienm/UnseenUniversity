#!/usr/bin/env python3
"""m_devlab_intent.py — Add devlab.predictions and devlab.validations tables.

Usage:
    python3 unseen_university/migrations/m_devlab_intent.py

Idempotent: safe to run multiple times.

Schema:
    devlab.predictions
        id              UUID PRIMARY KEY
        context         TEXT NOT NULL
        domain          TEXT NOT NULL
        predicted_intent TEXT NOT NULL
        confidence      FLOAT NOT NULL
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()

    devlab.validations
        id              UUID PRIMARY KEY
        prediction_id   UUID NULLABLE FK->devlab.predictions
        actual_outcome  TEXT NOT NULL
        match           BOOLEAN              -- NULL when no prior predict() call
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import psycopg2

log = logging.getLogger(__name__)

_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    os.environ.get("IGOR_HOME_DB_URL", ""),
)

_SQL = """
CREATE SCHEMA IF NOT EXISTS devlab;

CREATE TABLE IF NOT EXISTS devlab.predictions (
    id               UUID PRIMARY KEY,
    context          TEXT NOT NULL,
    domain           TEXT NOT NULL,
    predicted_intent TEXT NOT NULL,
    confidence       FLOAT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS devlab.validations (
    id             UUID PRIMARY KEY,
    prediction_id  UUID REFERENCES devlab.predictions(id),
    actual_outcome TEXT NOT NULL,
    match          BOOLEAN,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS intent_predictions_domain ON devlab.predictions (domain);
CREATE INDEX IF NOT EXISTS intent_predictions_created ON devlab.predictions (created_at DESC);
CREATE INDEX IF NOT EXISTS intent_validations_pred_id ON devlab.validations (prediction_id);
CREATE INDEX IF NOT EXISTS intent_validations_created ON devlab.validations (created_at DESC);
"""


def migrate() -> None:
    if not _DB_URL:
        raise RuntimeError("Set UU_HOME_DB_URL or IGOR_HOME_DB_URL before running this migration")
    conn = psycopg2.connect(_DB_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_SQL)
        log.info("devlab_intent migration completed")
    finally:
        conn.close()


def verify() -> bool:
    try:
        conn = psycopg2.connect(_DB_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'devlab' AND table_name IN ('predictions', 'validations') "
                "ORDER BY table_name;"
            )
            tables = {row[0] for row in cur.fetchall()}
        conn.close()
        missing = {"predictions", "validations"} - tables
        if missing:
            log.error("Missing tables: %s", missing)
            return False
        log.info("devlab_intent schema verified")
        return True
    except Exception as e:
        log.error("Verification failed: %s", e)
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    migrate()
    if verify():
        log.info("devlab_intent migration verified")
        sys.exit(0)
    else:
        log.error("devlab_intent migration verification failed")
        sys.exit(1)


if __name__ == "__main__":
    main()

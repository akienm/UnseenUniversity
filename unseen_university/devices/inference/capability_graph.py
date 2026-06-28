"""
capability_graph.py — Per-model performance data for the model eval harness.

Table: adc.model_eval_results
  Stores quality score, latency, and cost per (task, model) combination so
  callers can characterise which model handles which task class best.

Public functions:
  ensure_table(db_url)       — idempotent CREATE TABLE IF NOT EXISTS
  insert_result(db_url, ...) — record one model-run result row
  query_results(db_url, ...) — filter and retrieve results

No-op / returns [] when DB is unavailable; callers never need to guard.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _connect(db_url: str):
    import psycopg2

    return psycopg2.connect(db_url)


def ensure_table(db_url: str) -> None:
    """Idempotent: create adc.model_eval_results if absent. Logs errors, never raises."""
    try:
        conn = _connect(db_url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS adc.model_eval_results (
                        id            TEXT PRIMARY KEY,
                        run_group_id  TEXT NOT NULL,
                        task_class    TEXT NOT NULL DEFAULT '',
                        model         TEXT NOT NULL,
                        provider      TEXT NOT NULL DEFAULT '',
                        task_text     TEXT NOT NULL,
                        output_text   TEXT,
                        quality_score FLOAT,
                        verdict       TEXT,
                        eval_id       TEXT,
                        latency_ms    INTEGER,
                        input_tokens  INTEGER,
                        output_tokens INTEGER,
                        cost_usd      FLOAT,
                        ran_at        TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
            conn.commit()
        finally:
            conn.close()
        log.info("capability_graph: adc.model_eval_results ensured")
    except Exception as exc:
        log.warning("capability_graph: ensure_table failed: %s", exc)


def insert_result(
    db_url: str,
    *,
    result_id: str,
    run_group_id: str,
    task_class: str,
    model: str,
    provider: str,
    task_text: str,
    output_text: str,
    quality_score: float | None,
    verdict: str | None,
    eval_id: str | None,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None,
) -> None:
    """Insert one model-run result row. No-op on error (logs warning)."""
    log.info(
        "capability_graph: insert result=%s model=%s group=%s quality=%s cost_usd=%s",
        result_id,
        model,
        run_group_id,
        quality_score,
        cost_usd,
    )
    try:
        conn = _connect(db_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO adc.model_eval_results
                        (id, run_group_id, task_class, model, provider,
                         task_text, output_text, quality_score, verdict, eval_id,
                         latency_ms, input_tokens, output_tokens, cost_usd)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        result_id,
                        run_group_id,
                        task_class,
                        model,
                        provider,
                        task_text[:4000],
                        (output_text or "")[:4000],
                        quality_score,
                        verdict,
                        eval_id,
                        latency_ms,
                        input_tokens,
                        output_tokens,
                        cost_usd,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.warning("capability_graph: insert_result failed: %s", exc)


def query_results(
    db_url: str,
    task_class: str = "",
    model: str = "",
    limit: int = 50,
) -> list[dict]:
    """Query model eval results, optionally filtered by task_class and/or model.

    Returns [] when DB is unavailable or the table doesn't exist yet.
    """
    log.info(
        "capability_graph: query task_class=%r model=%r limit=%d",
        task_class,
        model,
        limit,
    )
    try:
        conn = _connect(db_url)
        try:
            conditions: list[str] = []
            params: list = []
            if task_class:
                conditions.append("task_class = %s")
                params.append(task_class)
            if model:
                conditions.append("model = %s")
                params.append(model)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(limit)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, run_group_id, task_class, model, provider,
                           quality_score, verdict, latency_ms,
                           input_tokens, output_tokens, cost_usd, ran_at
                    FROM adc.model_eval_results
                    {where}
                    ORDER BY ran_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        log.warning("capability_graph: query_results failed: %s", exc)
        return []

    result = []
    for row in rows:
        eid, gid, tc, mdl, prov, qs, verd, lat, inp, out, cost, ran = row
        ts = ran.isoformat() if hasattr(ran, "isoformat") else str(ran)
        result.append(
            {
                "id": eid,
                "run_group_id": gid,
                "task_class": tc,
                "model": mdl,
                "provider": prov,
                "quality_score": qs,
                "verdict": verd,
                "latency_ms": lat,
                "input_tokens": inp,
                "output_tokens": out,
                "cost_usd": cost,
                "ran_at": ts,
            }
        )
    return result

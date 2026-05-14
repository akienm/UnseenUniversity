"""action_log.py — Shared action audit trail for ADC tools.

Every ADC action tool (shell_exec, file_read/write, file_ticket,
propose_change) appends to adc.action_log so the nighttime auditor has a
complete record of autonomous activity.

Usage:
    from agent_datacenter.action_log import append_action
    append_action("librarian", "shell_exec", {"cmd": "ls"}, "exit_code=0", 42, 0)

append_action is fire-and-forget — it never raises. A failed write is logged
at DEBUG and silently dropped so callers are never blocked.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_DDL = """
CREATE TABLE IF NOT EXISTS adc.action_log (
    id             SERIAL PRIMARY KEY,
    device_id      TEXT        NOT NULL,
    tool_name      TEXT        NOT NULL,
    args_json      JSONB       NOT NULL DEFAULT '{}',
    result_summary TEXT        NOT NULL DEFAULT '',
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_ms    INT,
    exit_code      INT
)
"""


def _conn():
    import psycopg2

    return psycopg2.connect(_PG_URL)


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_DDL)


def append_action(
    device_id: str,
    tool_name: str,
    args_dict: dict | None,
    result_summary: str,
    duration_ms: int | None = None,
    exit_code: int | None = None,
) -> None:
    """Append one action record to adc.action_log. Never raises."""
    try:
        conn = _conn()
        try:
            with conn:
                _ensure_table(conn)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO adc.action_log
                            (device_id, tool_name, args_json, result_summary,
                             duration_ms, exit_code)
                        VALUES (%s, %s, %s::jsonb, %s, %s, %s)
                        """,
                        (
                            device_id,
                            tool_name,
                            json.dumps(args_dict or {}),
                            result_summary or "",
                            duration_ms,
                            exit_code,
                        ),
                    )
        finally:
            conn.close()
    except Exception as exc:
        log.debug("action_log.append_action failed (non-fatal): %s", exc)

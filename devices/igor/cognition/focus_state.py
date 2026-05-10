"""
focus_state.py — cross-cycle focus persistence for clan.memories activation.

Single-row upsert in instance.focus_state (id=1). Tracks the highest-activated
memory node across NE cycles with displacement hysteresis and expiry.

API:
  get_focus()                          → dict|None
  update_from_activation(memory_id, score)   called by activate()
  advance_cycle()                      → bool  True if focus expired this call
  reset_focus()

Displacement hysteresis: incoming score must be >= current * HYSTERESIS_FACTOR
to displace current focus. Prevents thrashing on noise.

Committed focus expires when ne_cycle_counter reaches expires_at_cycle.
focus_history is a JSONB ring buffer capped at HISTORY_CAP entries.

T-igor-focus-state / D-activate-primitive-2026-05-10
"""

from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger(__name__)

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

HYSTERESIS_FACTOR: float = 1.2
EXPIRY_CYCLES: int = 5
HISTORY_CAP: int = 5

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS instance.focus_state (
    id                  serial PRIMARY KEY,
    memory_id           text,
    activation_score    float DEFAULT 0.0,
    status              text DEFAULT 'candidate'
                            CHECK (status IN ('candidate', 'committed')),
    committed_at        timestamptz,
    expires_at_cycle    int,
    ne_cycle_counter    int NOT NULL DEFAULT 0,
    focus_history       jsonb NOT NULL DEFAULT '[]'::jsonb
)
"""


def _conn():
    import psycopg2

    return psycopg2.connect(_PG_URL)


def _ensure_table() -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_SQL)
    finally:
        conn.close()


def get_focus() -> dict | None:
    """Return the current focus row as a dict, or None if no focus is set."""
    _ensure_table()
    conn = _conn()
    try:
        import psycopg2.extras

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM instance.focus_state WHERE id = 1")
            row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_from_activation(memory_id: str, score: float) -> None:
    """Update focus from an activate() result.

    Inserts first focus as 'candidate'. Displaces current focus only when
    score >= current_score * HYSTERESIS_FACTOR.
    Appends to focus_history (ring buffer, capped at HISTORY_CAP).
    """
    _ensure_table()
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT memory_id, activation_score, focus_history "
                    "FROM instance.focus_state WHERE id = 1"
                )
                row = cur.fetchone()

                history_entry = {
                    "memory_id": memory_id,
                    "score": score,
                    "ts": time.time(),
                }

                if row is None:
                    history = json.dumps([history_entry])
                    cur.execute(
                        "INSERT INTO instance.focus_state "
                        "(id, memory_id, activation_score, status, focus_history) "
                        "VALUES (1, %s, %s, 'candidate', %s::jsonb)",
                        (memory_id, score, history),
                    )
                    return

                current_id, current_score, current_history = row
                current_score = float(current_score or 0.0)

                history = current_history if isinstance(current_history, list) else []
                if isinstance(history, str):
                    try:
                        history = json.loads(history)
                    except Exception:
                        history = []
                history.append(history_entry)
                if len(history) > HISTORY_CAP:
                    history = history[-HISTORY_CAP:]

                if current_id is None or score >= current_score * HYSTERESIS_FACTOR:
                    cur.execute(
                        "UPDATE instance.focus_state "
                        "SET memory_id = %s, activation_score = %s, "
                        "    status = 'candidate', focus_history = %s::jsonb "
                        "WHERE id = 1",
                        (memory_id, score, json.dumps(history)),
                    )
                else:
                    cur.execute(
                        "UPDATE instance.focus_state "
                        "SET focus_history = %s::jsonb WHERE id = 1",
                        (json.dumps(history),),
                    )
    finally:
        conn.close()


def advance_cycle() -> bool:
    """Increment ne_cycle_counter. Returns True if committed focus has expired.

    A committed focus expires when ne_cycle_counter reaches expires_at_cycle.
    On expiry: status reverts to 'candidate', expires_at_cycle cleared.
    """
    _ensure_table()
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, ne_cycle_counter, expires_at_cycle "
                    "FROM instance.focus_state WHERE id = 1"
                )
                row = cur.fetchone()

                if row is None:
                    return False

                status, cycle, expires_at = row
                new_cycle = (cycle or 0) + 1

                expired = (
                    status == "committed"
                    and expires_at is not None
                    and new_cycle >= expires_at
                )

                if expired:
                    cur.execute(
                        "UPDATE instance.focus_state "
                        "SET ne_cycle_counter = %s, status = 'candidate', "
                        "    expires_at_cycle = NULL "
                        "WHERE id = 1",
                        (new_cycle,),
                    )
                else:
                    cur.execute(
                        "UPDATE instance.focus_state "
                        "SET ne_cycle_counter = %s WHERE id = 1",
                        (new_cycle,),
                    )

        return expired
    finally:
        conn.close()


def reset_focus() -> None:
    """Clear the focus row entirely."""
    _ensure_table()
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM instance.focus_state WHERE id = 1")
    finally:
        conn.close()

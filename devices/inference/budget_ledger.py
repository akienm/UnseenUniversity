"""
budget_ledger.py — Per-agent inference spend tracking and budget enforcement.

Two tables in the clan schema (unqualified — routed by search_path):
  budget_ledger  — one row per inference call, all providers
  budget_limits  — agent-level session/overall limits

Provider modes:
  openrouter   — enforcing: pre-call limit check fires; cost_usd from OR response
  ollama       — tracking only: cost_usd=None; no limit check
  anthropic_max — tracking only: cost_usd=None; no limit check

Search path is read from IGOR_HOME_SEARCH_PATH (default: clan,infra,public),
so test fixtures that set IGOR_HOME_SEARCH_PATH=test_clan_<ts>,infra,public
redirect writes to the isolated test schema.

No-op throughout when IGOR_HOME_DB_URL is absent.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_ENFORCING_PROVIDERS = {"openrouter"}


def _db_url() -> str:
    return os.environ.get("IGOR_HOME_DB_URL", "")


def _search_path() -> str:
    return os.environ.get("IGOR_HOME_SEARCH_PATH", "clan,infra,public")


def _connect():
    """Return a psycopg2 connection with the correct search_path, or None."""
    db_url = _db_url()
    if not db_url:
        return None
    try:
        import psycopg2

        conn = psycopg2.connect(db_url)
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {_search_path()}")
        return conn
    except Exception as exc:
        log.debug("budget_ledger: connect failed — %s", exc)
        return None


def debit(
    agent_id: str,
    instance_id: str,
    coa_id: str,
    session_id: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None,
) -> None:
    """Insert one row into budget_ledger. No-op when DB is unavailable."""
    conn = _connect()
    if conn is None:
        return
    ts = datetime.now(tz=timezone.utc).isoformat()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO budget_ledger"
                    " (agent_id, instance_id, coa_id, session_id,"
                    "  provider, model, input_tokens, output_tokens, cost_usd, ts)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        agent_id,
                        instance_id,
                        coa_id,
                        session_id,
                        provider,
                        model,
                        input_tokens,
                        output_tokens,
                        cost_usd,
                        ts,
                    ),
                )
    except Exception as exc:
        log.debug("budget_ledger: debit failed — %s", exc)
    finally:
        conn.close()


def budget_summary(
    agent_id: str,
    group_by: str = "session",
) -> list[dict]:
    """
    Return aggregated spend for agent_id grouped by 'session', 'coa', or 'instance'.

    Each entry: {group_key, input_tokens, output_tokens, cost_usd_total, call_count}.
    cost_usd_total sums only non-NULL rows (OR calls).
    Returns [] when DB is unavailable.
    """
    valid_groups = {"session": "session_id", "coa": "coa_id", "instance": "instance_id"}
    if group_by not in valid_groups:
        raise ValueError(f"group_by must be one of {list(valid_groups)}")
    col = valid_groups[group_by]
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {col},"
                    "  SUM(input_tokens),"
                    "  SUM(output_tokens),"
                    "  SUM(cost_usd),"
                    "  COUNT(*)"
                    " FROM budget_ledger"
                    " WHERE agent_id = %s"
                    f" GROUP BY {col}"
                    f" ORDER BY {col}",
                    (agent_id,),
                )
                rows = cur.fetchall()
        return [
            {
                "group_key": r[0] or "",
                "input_tokens": r[1] or 0,
                "output_tokens": r[2] or 0,
                "cost_usd_total": float(r[3]) if r[3] is not None else 0.0,
                "call_count": r[4] or 0,
            }
            for r in rows
        ]
    except Exception as exc:
        log.debug("budget_ledger: budget_summary failed — %s", exc)
        return []
    finally:
        conn.close()


def budget_limit_set(agent_id: str, scope: str, limit_usd: float) -> bool:
    """
    Upsert a budget limit for agent_id at the given scope ('session' or 'overall').
    Returns True on success, False on DB error.
    """
    if scope not in ("session", "overall"):
        raise ValueError("scope must be 'session' or 'overall'")
    conn = _connect()
    if conn is None:
        return False
    ts = datetime.now(tz=timezone.utc).isoformat()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO budget_limits (agent_id, scope, limit_usd, updated_at)"
                    " VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (agent_id, scope)"
                    " DO UPDATE SET limit_usd = EXCLUDED.limit_usd,"
                    "               updated_at = EXCLUDED.updated_at",
                    (agent_id, scope, limit_usd, ts),
                )
        return True
    except Exception as exc:
        log.debug("budget_ledger: budget_limit_set failed — %s", exc)
        return False
    finally:
        conn.close()


def budget_remaining(agent_id: str, session_id: str) -> dict:
    """
    Return {remaining_usd, pct_used, limit_usd, spent_usd} for the given session.

    Uses the 'session' limit when set. When no limit is set, remaining_usd=None.
    Spent_usd sums only OR calls (cost_usd IS NOT NULL) for this agent + session.
    Returns a safe dict on DB error.
    """
    conn = _connect()
    if conn is None:
        return {
            "remaining_usd": None,
            "pct_used": 0.0,
            "limit_usd": None,
            "spent_usd": 0.0,
        }
    try:
        with conn:
            with conn.cursor() as cur:
                # Get session limit
                cur.execute(
                    "SELECT limit_usd FROM budget_limits"
                    " WHERE agent_id = %s AND scope = 'session'",
                    (agent_id,),
                )
                row = cur.fetchone()
                limit = float(row[0]) if row else None

                # Sum OR spend for this session
                cur.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0.0)"
                    " FROM budget_ledger"
                    " WHERE agent_id = %s AND session_id = %s"
                    "   AND cost_usd IS NOT NULL",
                    (agent_id, session_id),
                )
                spent = float(cur.fetchone()[0])

        if limit is None:
            return {
                "remaining_usd": None,
                "pct_used": 0.0,
                "limit_usd": None,
                "spent_usd": spent,
            }

        remaining = max(0.0, limit - spent)
        pct_used = min(100.0, (spent / limit * 100.0)) if limit > 0 else 0.0
        return {
            "remaining_usd": remaining,
            "pct_used": round(pct_used, 2),
            "limit_usd": limit,
            "spent_usd": spent,
        }
    except Exception as exc:
        log.debug("budget_ledger: budget_remaining failed — %s", exc)
        return {
            "remaining_usd": None,
            "pct_used": 0.0,
            "limit_usd": None,
            "spent_usd": 0.0,
        }
    finally:
        conn.close()


def check_session_limit(
    agent_id: str, session_id: str, provider: str
) -> tuple[bool, str]:
    """
    Pre-call gate for enforcing providers. Returns (ok, message).

    ok=False means: reject this call — session budget exhausted.
    Non-enforcing providers always return (True, "tracking-only").
    Fail-open when DB is unavailable.
    """
    if provider not in _ENFORCING_PROVIDERS:
        return True, "tracking-only"
    info = budget_remaining(agent_id, session_id)
    if info["remaining_usd"] is None:
        return True, "no session limit set"
    if info["remaining_usd"] <= 0:
        spent = info.get("spent_usd", 0.0)
        limit = info.get("limit_usd", 0.0)
        return (
            False,
            f"session budget exhausted: ${spent:.4f} spent >= ${limit:.4f} limit",
        )
    return True, f"${info['remaining_usd']:.4f} remaining"

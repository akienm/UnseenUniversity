"""
Granny Weatherwax health snapshot — zero-inference status aggregator.

Collects:
  active_cc_sessions: list of running cc-T-* tmux sessions + count
  queue_depth: ticket counts by status and by size
  orphaned_tickets: in_progress tickets with no matching tmux session
  routing_gaps: sprint-status tickets carrying tags that have no explicit
                routing edge (note: these still dispatch via the no-route
                catch-all in daemon.run_once — this is an observability
                signal, not a "won't run" condition)

Callable from the /health skill or the /api/granny/health web endpoint.
No LLM inference calls are made.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_DB_URL_DEFAULT = "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"


def _db_connect():
    import psycopg2
    import psycopg2.extras

    db_url = os.environ.get("IGOR_HOME_DB_URL", _DB_URL_DEFAULT)
    return psycopg2.connect(db_url, connect_timeout=5), psycopg2.extras.RealDictCursor


def _routable_tags() -> frozenset:
    """Return the set of tags with explicit routing edges (catch-all excluded).

    Built from _CC_TAGS (daemon), _DEFAULT_ROUTING keys (device), and 'minion'.
    Import at call time to avoid circular-import issues at module load.
    """
    try:
        from devices.granny.daemon import _CC_TAGS
        from devices.granny.device import _DEFAULT_ROUTING

        return frozenset(_CC_TAGS) | frozenset(_DEFAULT_ROUTING.keys()) | {"minion"}
    except Exception as e:
        log.warning("health: could not load routing tables: %s", e)
        return frozenset()


# ── Pure detection helpers (testable with synthetic data) ──────────────────────


def detect_orphans(in_progress: list[dict], sessions: list[str]) -> list[dict]:
    """Return in_progress tickets with no matching cc-{id} tmux session."""
    session_set = frozenset(sessions)
    return [t for t in in_progress if f"cc-{t.get('id', '')}" not in session_set]


def detect_routing_gaps(
    sprint_tickets: list[dict], routable: frozenset
) -> list[dict[str, Any]]:
    """Return gaps: tags carried by sprint tickets that have no explicit routing edge.

    Each gap entry: {"tag": str, "ticket_ids": list[str]}
    Tickets with at least one routable tag are not flagged — gaps appear only when
    ALL of a ticket's tags fall outside the known routing table.
    """
    from collections import defaultdict

    unrouted: dict[str, list[str]] = defaultdict(list)
    for ticket in sprint_tickets:
        tags = set(ticket.get("tags") or [])
        if not tags:
            continue
        if tags & routable:
            continue
        for tag in sorted(tags):
            unrouted[tag].append(ticket.get("id", ""))

    return [{"tag": tag, "ticket_ids": ids} for tag, ids in sorted(unrouted.items())]


# ── Main snapshot ──────────────────────────────────────────────────────────────


def granny_health_snapshot() -> dict:
    """Zero-inference operational health snapshot for Granny Weatherwax.

    Returns a structured dict suitable for JSON serialisation.
    Logs the call at INFO level.
    """
    from devices.granny.daemon import _list_cc_sessions

    now = datetime.now(timezone.utc).isoformat()
    log.info("granny_health_snapshot: called at %s", now)

    # ── 1. Active CC sessions ─────────────────────────────────────────────────
    sessions = _list_cc_sessions()
    active_cc = {"count": len(sessions), "sessions": sessions}

    # ── 2. Queue depth + 3. Orphan detection (single DB connection) ───────────
    queue_depth: dict[str, Any] = {"by_status": {}, "by_size": {}}
    orphaned: list[dict] = []
    routing_gaps: list[dict] = []
    db_error: str | None = None

    try:
        conn, CursorCls = _db_connect()
        try:
            with conn.cursor(cursor_factory=CursorCls) as cur:
                # Queue depth by status
                cur.execute(
                    """SELECT metadata->>'status' AS status, COUNT(*)::int AS count
                       FROM clan.memories
                       WHERE metadata->>'kind' = 'ticket'
                       GROUP BY 1 ORDER BY 2 DESC"""
                )
                queue_depth["by_status"] = {
                    r["status"]: r["count"] for r in cur.fetchall() if r["status"]
                }

                # Queue depth by size
                cur.execute("""SELECT metadata->>'size' AS size, COUNT(*)::int AS count
                       FROM clan.memories
                       WHERE metadata->>'kind' = 'ticket'
                       GROUP BY 1 ORDER BY 2 DESC""")
                queue_depth["by_size"] = {
                    r["size"]: r["count"] for r in cur.fetchall() if r["size"]
                }

                # In-progress tickets (for orphan detection)
                cur.execute("""SELECT metadata AS meta
                       FROM clan.memories
                       WHERE metadata->>'kind' = 'ticket'
                         AND metadata->>'status' = 'in_progress'""")
                in_progress = [dict(r["meta"]) for r in cur.fetchall()]
                orphaned = detect_orphans(in_progress, sessions)

                # Sprint tickets (for routing-gap detection)
                cur.execute("""SELECT metadata AS meta
                       FROM clan.memories
                       WHERE metadata->>'kind' = 'ticket'
                         AND metadata->>'status' = 'sprint'
                         AND (metadata->>'gate' IS NULL OR metadata->>'gate' = '')""")
                sprint_tickets = [dict(r["meta"]) for r in cur.fetchall()]
                routing_gaps = detect_routing_gaps(sprint_tickets, _routable_tags())
        finally:
            conn.close()
    except Exception as e:
        db_error = str(e)
        log.warning("granny_health_snapshot: db error: %s", e)

    snapshot = {
        "ts": now,
        "active_cc_sessions": active_cc,
        "queue_depth": queue_depth,
        "orphaned_tickets": orphaned,
        "routing_gaps": routing_gaps,
    }
    if db_error:
        snapshot["db_error"] = db_error

    log.info(
        "granny_health_snapshot: cc=%d orphans=%d gaps=%d",
        active_cc["count"],
        len(orphaned),
        len(routing_gaps),
    )
    return snapshot

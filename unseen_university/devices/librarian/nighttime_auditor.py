"""nighttime_auditor.py — Daily action log review for the Librarian.

Reads adc.action_log for the last 24h, identifies anomalies, and posts
one summary message to the 'shared' channel. Runs in a daemon background
thread at 02:00–04:00 local time. Started from mcp_server.serve() on
first initialize.
"""

from __future__ import annotations
from unseen_university.identity import home_db_url

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

_CC_SEND_URL = os.environ.get("CC_SEND_URL", "http://localhost:8082/api/cc_send")

_LOOP_INTERVAL_S = 300  # check every 5 min whether we're in the fire window
_FIRE_HOUR_START = 2  # 02:00 local
_FIRE_HOUR_END = 4  # 04:00 local
_HIGH_VOLUME_THRESHOLD = 50  # calls per tool in 24h → potential loop
_LARGE_WRITE_BYTES = 100 * 1024  # 100 KB

_started = False
_started_lock = threading.Lock()


def _conn():
    import psycopg2

    return psycopg2.connect(home_db_url())


def _fetch_window(hours: int = 24) -> list[dict]:
    """Return action_log rows from the last N hours."""
    try:
        import psycopg2.extras

        conn = _conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT tool_name, device_id, args_json, result_summary,
                           exit_code, duration_ms, ts
                    FROM adc.action_log
                    WHERE ts >= now() - interval '%s hours'
                    ORDER BY ts
                    """,
                    (hours,),
                )
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        log.error("nighttime_auditor: fetch_window failed: %s", e)
        return []


def _build_summary(rows: list[dict]) -> str:
    if not rows:
        return ""

    from collections import Counter

    tool_counts: Counter = Counter()
    failed: list[dict] = []
    large_writes: list[dict] = []
    tickets_filed: list[dict] = []

    for r in rows:
        tool_counts[r["tool_name"]] += 1
        if r.get("exit_code") not in (None, 0):
            failed.append(r)
        if r["tool_name"] == "file_write":
            args = r.get("args_json") or {}
            summary = r.get("result_summary") or ""
            # result_summary format: "written=N"
            try:
                written = int(summary.split("written=")[-1])
                if written > _LARGE_WRITE_BYTES:
                    large_writes.append({**r, "_written": written})
            except (ValueError, IndexError):
                pass
        if r["tool_name"] == "file_ticket":
            tickets_filed.append(r)

    high_volume = [t for t, c in tool_counts.items() if c > _HIGH_VOLUME_THRESHOLD]

    lines = [
        f"[nighttime-auditor] 24h action log summary — {len(rows)} total calls",
        "",
        "Call volume by tool:",
    ]
    for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        flag = " ⚠ HIGH VOLUME" if tool in high_volume else ""
        lines.append(f"  {tool}: {count}{flag}")

    if failed:
        lines += ["", f"Failed executions ({len(failed)}):"]
        for r in failed[:10]:
            lines.append(
                f"  [{r['ts']}] {r['tool_name']} exit={r['exit_code']}"
                f" — {str(r.get('args_json', ''))[:80]}"
            )
        if len(failed) > 10:
            lines.append(f"  ... and {len(failed) - 10} more")

    if large_writes:
        lines += ["", f"Large file writes ({len(large_writes)}):"]
        for r in large_writes[:5]:
            kb = r["_written"] // 1024
            lines.append(f"  {r.get('args_json', {}).get('path', '?')} — {kb}KB")

    if tickets_filed:
        lines += ["", f"Tickets filed autonomously ({len(tickets_filed)}):"]
        for r in tickets_filed[:10]:
            args = r.get("args_json") or {}
            lines.append(f"  {args.get('ticket_id', '?')} — {args.get('title', '?')}")

    if high_volume:
        lines += [
            "",
            f"⚠ Potential loop detected: {', '.join(high_volume)} exceeded {_HIGH_VOLUME_THRESHOLD} calls",
        ]

    return "\n".join(lines)


def _post_to_channel(message: str) -> None:
    import json as _json
    import ssl
    import urllib.request

    payload = _json.dumps({"content": message, "session_id": "shared"}).encode()
    req = urllib.request.Request(
        _CC_SEND_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx):
            pass
    except Exception as e:
        log.error("nighttime_auditor: channel post failed: %s", e)


def _in_fire_window() -> bool:
    hour = time.localtime().tm_hour
    return _FIRE_HOUR_START <= hour < _FIRE_HOUR_END


def run_audit() -> str | None:
    """Run one audit pass. Returns the summary string, or None when log is empty."""
    rows = _fetch_window(hours=24)
    if not rows:
        log.info("nighttime_auditor: no action_log entries in last 24h — skipping")
        return None
    summary = _build_summary(rows)
    _post_to_channel(summary)
    log.info(
        "nighttime_auditor: posted summary (%d rows, %d chars)", len(rows), len(summary)
    )
    return summary


def _auditor_loop() -> None:
    """Background daemon loop: sleep until fire window, run once, repeat next day."""
    fired_today: int | None = None  # day-of-year when last fired

    while True:
        time.sleep(_LOOP_INTERVAL_S)
        now = time.localtime()
        today = now.tm_yday
        if _in_fire_window() and fired_today != today:
            try:
                run_audit()
            except Exception as e:
                log.error("nighttime_auditor: run_audit error: %s", e)
            fired_today = today


def start_nighttime_auditor() -> None:
    """Start the background auditor thread. One-shot guard — safe to call repeatedly."""
    global _started
    with _started_lock:
        if _started:
            return
        _started = True
    t = threading.Thread(target=_auditor_loop, daemon=True, name="nighttime-auditor")
    t.start()
    log.info("nighttime_auditor: started")

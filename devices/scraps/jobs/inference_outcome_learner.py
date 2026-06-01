"""
inference_outcome_learner.py — Scraps job: aggregate MINION_RESULT outcomes.

Periodic job (not a daemon). Reads MINION_RESULT + GRANNY_DISPATCH posts from
the shared channel, aggregates per (tag, task_class, size), writes a flat JSON
report, and posts a summary to channel.

Run: python -m devices.scraps.jobs.inference_outcome_learner
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_LIMIT = 200
_CURSOR_PATH = (
    Path(os.environ.get("GRANNY_HOME", str(Path.home() / ".granny")))
    / "outcome_learner_cursor.txt"
)
_REPORT_PATH = (
    Path(os.environ.get("GRANNY_HOME", str(Path.home() / ".granny")))
    / "outcome_report.json"
)
_PG_URL = os.environ.get("IGOR_HOME_DB_URL", "")

# Signals that represent the advisor-loop perspective (stored in advisor_signal field).
_ADVISOR_SIGNALS = {"REPROMPT", "UPGRADE", "BLOCKED", "CONFUSED", "ESCALATE"}


# ── Parsing helpers ───────────────────────────────────────────────────────────


def _parse_kv(content: str) -> dict[str, str]:
    """Parse pipe-separated KEY=val pairs after the leading keyword token.

    e.g. "MINION_RESULT|ticket=T-x|signal=DONE|cost_usd=0.01"
    → {"ticket": "T-x", "signal": "DONE", "cost_usd": "0.01"}
    """
    parts = content.split("|")
    result: dict[str, str] = {}
    for part in parts[1:]:  # skip leading keyword
        if "=" in part:
            k, _, v = part.partition("=")
            result[k.strip()] = v.strip()
    return result


def parse_minion_result(content: str) -> dict[str, Any] | None:
    """Parse a MINION_RESULT channel message body. Returns None on malformed input."""
    if not content.startswith("MINION_RESULT|"):
        return None
    kv = _parse_kv(content)
    ticket = kv.get("ticket", "")
    if not ticket:
        return None
    try:
        return {
            "ticket": ticket,
            "signal": kv.get("signal", ""),
            "task_class": kv.get("task_class", ""),
            "iterations": int(kv.get("iterations", 0)),
            "rounds": int(kv.get("rounds", 0)),
            "advisor_calls": int(kv.get("advisor_calls", 0)),
            "cost_usd": float(kv.get("cost_usd", 0.0)),
            "tokens_in": int(kv.get("tokens_in", 0)),
            "tokens_out": int(kv.get("tokens_out", 0)),
            "advisor_signal": kv.get("advisor_signal"),
        }
    except (ValueError, TypeError):
        return None


def parse_dispatch(content: str) -> dict[str, Any] | None:
    """Parse a GRANNY_DISPATCH channel message body. Returns None on malformed input."""
    if not content.startswith("GRANNY_DISPATCH|"):
        return None
    kv = _parse_kv(content)
    ticket = kv.get("ticket", "")
    if not ticket:
        return None
    tags_raw = kv.get("tags", "")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    return {
        "ticket": ticket,
        "worker": kv.get("worker", ""),
        "size": kv.get("size", "?"),
        "tags": tags,
    }


# ── Cursor ────────────────────────────────────────────────────────────────────


def _load_cursor(cursor_path: Path) -> int | None:
    """Read last-seen message id from cursor file. Returns None if absent."""
    try:
        text = cursor_path.read_text().strip()
        return int(text) if text else None
    except (FileNotFoundError, ValueError):
        return None


def _save_cursor(cursor_path: Path, last_id: int) -> None:
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(str(last_id))
    log.info("outcome_learner: cursor saved → id=%d (%s)", last_id, cursor_path)


# ── Channel fetch ─────────────────────────────────────────────────────────────


def _fetch_granny_messages(
    pg_url: str,
    since_id: int | None,
    limit: int,
) -> list[dict]:
    """Fetch granny-weatherwax messages from the shared channel in one query.

    Returns list of {id, content} dicts ordered by id ascending.
    Raises on DB errors — caller handles gracefully.
    """
    import psycopg2
    import psycopg2.extras

    params: list[Any] = ["granny-weatherwax", "shared"]
    where_extra = ""
    if since_id is not None:
        where_extra = " AND id > %s"
        params.append(since_id)
    params.append(limit)

    sql = (
        "SELECT id, content FROM channel_messages"
        " WHERE author = %s AND channel = %s"
        + where_extra
        + " ORDER BY id ASC LIMIT %s"
    )
    log.info(
        "outcome_learner: channel read — since_id=%s limit=%d",
        since_id,
        limit,
    )
    with psycopg2.connect(pg_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


# ── Aggregation ───────────────────────────────────────────────────────────────


def _normalize_signal(signal: str) -> str:
    """Collapse ESCALATE: worker/analyst/designer → ESCALATE."""
    if signal.upper().startswith("ESCALATE"):
        return "ESCALATE"
    return signal.upper()


def aggregate(
    result_records: list[dict],
    dispatch_map: dict[str, dict],
) -> list[dict]:
    """Compute per-(tag, task_class, size) stats.

    result_records: list of parse_minion_result() output dicts.
    dispatch_map: ticket_id → parse_dispatch() output dict.

    Tickets with no matching dispatch are bucketed under tag="unknown", size="?".
    Returns a list of aggregate dicts (one per key).
    """
    # bucket[key] = {total, DONE, ESCALATE, REPROMPT, UPGRADE, BLOCKED, CONFUSED,
    #                sum_iterations, sum_cost_usd}
    bucket: dict[tuple, dict] = defaultdict(
        lambda: {
            "total": 0,
            "DONE": 0,
            "ESCALATE": 0,
            "REPROMPT": 0,
            "UPGRADE": 0,
            "BLOCKED": 0,
            "CONFUSED": 0,
            "sum_iterations": 0,
            "sum_cost_usd": 0.0,
        }
    )

    for rec in result_records:
        ticket = rec["ticket"]
        task_class = rec.get("task_class", "")
        signal = _normalize_signal(rec.get("signal", ""))
        advisor_signal = rec.get("advisor_signal") or ""
        iterations = rec.get("iterations", 0)
        cost_usd = rec.get("cost_usd", 0.0)

        dispatch = dispatch_map.get(ticket)
        if dispatch:
            tags = dispatch.get("tags") or ["unknown"]
            size = dispatch.get("size", "?")
        else:
            tags = ["unknown"]
            size = "?"

        for tag in tags:
            key = (tag, task_class, size)
            b = bucket[key]
            b["total"] += 1
            if signal in (
                "DONE",
                "ESCALATE",
                "REPROMPT",
                "UPGRADE",
                "BLOCKED",
                "CONFUSED",
            ):
                b[signal] = b.get(signal, 0) + 1
            # Also count advisor_signal in the appropriate bucket.
            adv = advisor_signal.upper()
            if adv in ("REPROMPT", "UPGRADE", "BLOCKED", "CONFUSED"):
                b[adv] = b.get(adv, 0) + 1
            b["sum_iterations"] += iterations
            b["sum_cost_usd"] += cost_usd

    out = []
    for (tag, task_class, size), b in sorted(bucket.items()):
        total = b["total"]
        out.append(
            {
                "tag": tag,
                "task_class": task_class,
                "size": size,
                "total": total,
                "done_pct": round(b["DONE"] / total * 100, 1) if total else 0.0,
                "escalate_pct": round(b["ESCALATE"] / total * 100, 1) if total else 0.0,
                "reprompt_pct": round(b["REPROMPT"] / total * 100, 1) if total else 0.0,
                "upgrade_pct": round(b["UPGRADE"] / total * 100, 1) if total else 0.0,
                "blocked_pct": round(b["BLOCKED"] / total * 100, 1) if total else 0.0,
                "confused_pct": round(b["CONFUSED"] / total * 100, 1) if total else 0.0,
                "avg_iterations": (
                    round(b["sum_iterations"] / total, 2) if total else 0.0
                ),
                "avg_cost_usd": round(b["sum_cost_usd"] / total, 5) if total else 0.0,
            }
        )
    return out


# ── Insights ──────────────────────────────────────────────────────────────────


def top_insights(aggregates: list[dict], n: int = 3) -> list[str]:
    """Pick the top N most actionable routing insights.

    Prioritises high-volume keys with high escalate_pct first.
    """
    if not aggregates:
        return ["No outcome data available — run minion tasks to populate"]

    # Sort: high escalate_pct + high volume first; done-only keys as low priority.
    ranked = sorted(
        aggregates,
        key=lambda a: (-(a["escalate_pct"]), -(a["total"])),
    )

    insights = []
    for a in ranked[:n]:
        key = f"{a['tag']}/{a['size']}/{a['task_class']}"
        if a["escalate_pct"] >= 50:
            action = "try analyst tier"
            insights.append(
                f"{key}: {a['escalate_pct']}% escalate ({a['total']} runs) — {action}"
            )
        elif a["done_pct"] >= 80:
            insights.append(
                f"{key}: {a['done_pct']}% done ({a['total']} runs) — routing OK"
            )
        else:
            insights.append(
                f"{key}: {a['done_pct']}% done / {a['escalate_pct']}% escalate ({a['total']} runs)"
            )

    return insights if insights else ["Insufficient data for insights"]


# ── Report format ─────────────────────────────────────────────────────────────


def format_report(
    aggregates: list[dict],
    insights: list[str],
    meta: dict,
) -> dict:
    """Produce the JSON-serialisable report dict."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": meta,
        "aggregates": aggregates,
        "insights": insights,
    }


def _report_channel_message(report: dict) -> str:
    """Format the OUTCOME_LEARNER_REPORT channel post."""
    w = report["window"]
    insights = report.get("insights", [])
    parts = [
        f"OUTCOME_LEARNER_REPORT|window={w.get('limit', '?')}|results={w.get('result_count', 0)}",
    ]
    for i, ins in enumerate(insights[:3], 1):
        parts.append(f"insight_{i}={ins}")
    return "|".join(parts)


# ── Public API ────────────────────────────────────────────────────────────────


def outcome_summary(report_path: Path | None = None) -> str:
    """Return a one-line summary from the persisted report. Safe to call with no DB."""
    path = report_path or _REPORT_PATH
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return "Outcome: no report yet"
    insights = data.get("insights", [])
    result_count = data.get("window", {}).get("result_count", 0)
    top = insights[0] if insights else "—"
    return f"Outcome: {result_count} results — {top}"


def run(
    pg_url: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    cursor_path: Path | None = None,
    report_path: Path | None = None,
) -> dict:
    """Main entry point for the outcome learner job.

    Reads MINION_RESULT and GRANNY_DISPATCH messages from the shared channel,
    aggregates per (tag, task_class, size), writes report_path, and posts a
    summary to channel.

    Returns the report dict. Gracefully returns an empty report when the DB
    is unreachable.
    """
    resolved_pg = pg_url or _PG_URL
    resolved_cursor = cursor_path or _CURSOR_PATH
    resolved_report = report_path or _REPORT_PATH

    since_id = _load_cursor(resolved_cursor)

    rows: list[dict] = []
    if resolved_pg:
        try:
            rows = _fetch_granny_messages(resolved_pg, since_id, limit)
        except Exception as exc:
            log.warning("outcome_learner: channel fetch failed: %s", exc)

    # Partition into MINION_RESULT records and a dispatch map by ticket_id.
    result_records: list[dict] = []
    dispatch_map: dict[str, dict] = {}
    max_id: int = since_id or 0

    for row in rows:
        row_id = row.get("id", 0)
        content = row.get("content", "") or ""
        if row_id and row_id > max_id:
            max_id = row_id
        if content.startswith("MINION_RESULT|"):
            parsed = parse_minion_result(content)
            if parsed:
                result_records.append(parsed)
        elif content.startswith("GRANNY_DISPATCH|"):
            parsed = parse_dispatch(content)
            if parsed:
                # Keep latest dispatch per ticket (last one wins).
                dispatch_map[parsed["ticket"]] = parsed

    aggregates = aggregate(result_records, dispatch_map)
    insights = top_insights(aggregates)
    meta = {
        "since_id": since_id,
        "limit": limit,
        "result_count": len(result_records),
        "row_count": len(rows),
    }
    report = format_report(aggregates, insights, meta)

    # Write flat JSON report.
    resolved_report.parent.mkdir(parents=True, exist_ok=True)
    resolved_report.write_text(json.dumps(report, indent=2))
    log.info(
        "outcome_learner: report written → %s (%d aggregates)",
        resolved_report,
        len(aggregates),
    )

    # Post summary to channel — best-effort.
    if resolved_pg:
        try:
            from unseen_university.channel import post_to_channel

            msg = _report_channel_message(report)
            post_to_channel(msg, author="scraps-outcome-learner", channel="shared")
            log.info("outcome_learner: OUTCOME_LEARNER_REPORT posted to channel")
        except Exception as exc:
            log.warning("outcome_learner: channel post failed: %s", exc)

    # Advance cursor to the highest id seen.
    if max_id and max_id > (since_id or 0):
        _save_cursor(resolved_cursor, max_id)

    return report


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    report = run()
    print(json.dumps(report, indent=2))
    sys.exit(0)

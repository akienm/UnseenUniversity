"""
audit_telemetry.py — Structured per-run record for the audit pyramid.

Every audit skill (audit-design, audit-ticket, audit-precode, audit-smell,
audit-debris, audit-day, audit-expert, audit-audits) calls emit_run_record()
at the end of each run. Records land in the palace under:

  unseenuniversity/audits/<level>/runs/<YYYY-MM-DD-HHMMSS>

Watch-for notes (patterns to check on next run) are stored under:

  unseenuniversity/audits/<level>/watch_next/<id>

with a TTL field in content. read_runs() returns records within a time window.

Record schema (YAML in node content):
  level, ran_at, inputs_examined, checks_fired, checks_passed, checks_amended,
  checks_discarded, findings[{check, severity, file_or_target, matched_pattern,
  upstream_layer, overridden}], duration_seconds, tokens_used, model,
  watch_next_written, watch_next_hit, watch_next_aged, watch_next_expired, notes

Updated 2026-04-29T00:00:00Z
"""

from __future__ import annotations
from unseen_university.identity import home_db_url

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


_SEARCH_PATH = os.environ.get("IGOR_HOME_SEARCH_PATH") or "clan,infra,public"

VALID_LEVELS = frozenset(
    {"design", "ticket", "precode", "smell", "debris", "day", "expert", "audits"}
)


@dataclass
class AuditFinding:
    check: str
    severity: str  # high | med | low
    file_or_target: str = ""
    matched_pattern: str = ""
    upstream_layer: str = ""
    overridden: bool = False


@dataclass
class AuditRunRecord:
    level: str
    ran_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    inputs_examined: int = 0
    checks_fired: int = 0
    checks_passed: int = 0
    checks_amended: int = 0
    checks_discarded: int = 0
    findings: list[AuditFinding] = field(default_factory=list)
    duration_seconds: float = 0.0
    tokens_used: int = 0
    model: str = ""
    watch_next_written: int = 0
    watch_next_hit: int = 0
    watch_next_aged: int = 0
    watch_next_expired: int = 0
    notes: str = ""

    def to_yaml(self) -> str:
        lines = [
            f"level: {self.level}",
            f"ran_at: {self.ran_at}",
            f"inputs_examined: {self.inputs_examined}",
            f"checks_fired: {self.checks_fired}",
            f"checks_passed: {self.checks_passed}",
            f"checks_amended: {self.checks_amended}",
            f"checks_discarded: {self.checks_discarded}",
            f"duration_seconds: {self.duration_seconds}",
            f"tokens_used: {self.tokens_used}",
            f"model: {self.model}",
            f"watch_next_written: {self.watch_next_written}",
            f"watch_next_hit: {self.watch_next_hit}",
            f"watch_next_aged: {self.watch_next_aged}",
            f"watch_next_expired: {self.watch_next_expired}",
        ]
        if self.findings:
            lines.append("findings:")
            for f in self.findings:
                lines.append(f"  - check: {f.check}")
                lines.append(f"    severity: {f.severity}")
                if f.file_or_target:
                    lines.append(f"    file_or_target: {f.file_or_target}")
                if f.matched_pattern:
                    lines.append(f"    matched_pattern: {f.matched_pattern}")
                if f.upstream_layer:
                    lines.append(f"    upstream_layer: {f.upstream_layer}")
                if f.overridden:
                    lines.append(f"    overridden: true")
        if self.notes:
            lines.append(f"notes: |\n  {self.notes.strip()}")
        return "\n".join(lines)


def _connect():
    import psycopg2
    conn = psycopg2.connect(home_db_url())
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"SET search_path TO {_SEARCH_PATH}")
    cur.close()
    return conn


def _ensure_palace_node(conn, path: str, parent_path: str, title: str, content: str):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO memory_palace (path, parent_path, title, content, updated_at, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (path) DO UPDATE
          SET content = EXCLUDED.content, updated_at = EXCLUDED.updated_at
        """,
        (path, parent_path, title, content, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "audit_telemetry"),
    )
    cur.close()


def emit_run_record(level: str, record: AuditRunRecord) -> str:
    """
    Write a per-run audit record to the palace.
    Returns the palace path of the new node.
    Raises ValueError for unknown levels.
    """
    if level not in VALID_LEVELS:
        raise ValueError(f"Unknown audit level {level!r}. Valid: {sorted(VALID_LEVELS)}")

    ts = record.ran_at.replace(":", "").replace("-", "").replace("T", "-").replace("Z", "")
    run_id = ts[:15]  # YYYYMMDD-HHMMSS
    path = f"unseenuniversity/audits/{level}/runs/{run_id}"
    parent = f"unseenuniversity/audits/{level}/runs"

    conn = _connect()
    # Ensure parent nodes exist
    _ensure_palace_node(conn, f"unseenuniversity/audits/{level}", "unseenuniversity/audits",
                        f"audit-{level} records", f"Run records and watch_next for audit-{level}.")
    _ensure_palace_node(conn, parent, f"unseenuniversity/audits/{level}",
                        f"audit-{level} runs", "Per-run records indexed by timestamp.")
    _ensure_palace_node(conn, path, parent,
                        f"audit-{level} run {run_id}",
                        record.to_yaml())
    conn.close()
    return path


def emit_watch_next(level: str, note: str, ttl_days: int = 14, watch_id: str | None = None) -> str:
    """
    Write a watch-for note under unseenuniversity/audits/<level>/watch_next/<id>.
    TTL is stored in content; expiry is enforced by read_watch_next().
    Returns the palace path.
    """
    if level not in VALID_LEVELS:
        raise ValueError(f"Unknown audit level {level!r}")

    wid = watch_id or uuid.uuid4().hex[:8]
    path = f"unseenuniversity/audits/{level}/watch_next/{wid}"
    parent = f"unseenuniversity/audits/{level}/watch_next"
    now = datetime.now(timezone.utc)
    content = (
        f"written_at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"ttl_days: {ttl_days}\n"
        f"note: |\n  {note.strip()}\n"
        f"hit: false\n"
        f"aged: false\n"
    )
    conn = _connect()
    _ensure_palace_node(conn, f"unseenuniversity/audits/{level}", "unseenuniversity/audits",
                        f"audit-{level} records", f"Run records and watch_next for audit-{level}.")
    _ensure_palace_node(conn, parent, f"unseenuniversity/audits/{level}",
                        f"audit-{level} watch_next", "Watch-for notes for next run.")
    _ensure_palace_node(conn, path, parent, f"watch {wid}", content)
    conn.close()
    return path


def read_runs(level: str, since_days: int = 7) -> list[dict[str, Any]]:
    """
    Return run records for <level> written in the last <since_days> days.
    Each result is a dict with path, title, content (raw YAML), updated_at.
    """
    if level not in VALID_LEVELS:
        raise ValueError(f"Unknown audit level {level!r}")

    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    prefix = f"unseenuniversity/audits/{level}/runs/"
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT path, title, content, updated_at FROM memory_palace "
        "WHERE path LIKE %s AND updated_at >= %s ORDER BY path DESC",
        (prefix + "%", cutoff),
    )
    rows = [{"path": r[0], "title": r[1], "content": r[2], "updated_at": r[3]} for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def read_watch_next(level: str, include_expired: bool = False) -> list[dict[str, Any]]:
    """
    Return active watch_next notes for <level>.
    Expired notes (written_at + ttl_days < now) are excluded by default.
    """
    if level not in VALID_LEVELS:
        raise ValueError(f"Unknown audit level {level!r}")

    prefix = f"unseenuniversity/audits/{level}/watch_next/"
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT path, title, content, updated_at FROM memory_palace WHERE path LIKE %s ORDER BY path",
        (prefix + "%",),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    results = []
    now = datetime.now(timezone.utc)
    for path, title, content, updated_at in rows:
        entry = {"path": path, "title": title, "content": content, "updated_at": updated_at}
        # Parse written_at + ttl_days from YAML content (simple line scan)
        written_at = None
        ttl_days = 14
        for line in content.splitlines():
            if line.startswith("written_at:"):
                try:
                    written_at = datetime.fromisoformat(line.split(":", 1)[1].strip().replace("Z", "+00:00"))
                except ValueError:
                    pass
            if line.startswith("ttl_days:"):
                try:
                    ttl_days = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        if written_at:
            from datetime import timedelta
            expired = now > written_at + timedelta(days=ttl_days)
            entry["expired"] = expired
            if expired and not include_expired:
                continue
        results.append(entry)
    return results

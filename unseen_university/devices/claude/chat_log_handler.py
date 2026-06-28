"""
chat_log_handler.py — logging.Handler + Formatter for CC chat transcripts.

Implements unseen_university's logging interface (BaseDevice contract) for
Claude Code session transcripts. JSONL events (user/assistant turns) are
ingested as LogRecords and written to date-partitioned markdown at:
    $UNSEEN_UNIVERSITY_HOME/logs/CC.0/YYYY-MM-DD.md

Format matches export_chat.py (### User — / ### Assistant — with full datetime).
Each day file is rebuilt from scratch on flush() — same idempotent model as
export_chat.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

try:
    from devlab.claudecode.ts_format import format_display, parse_iso
except ImportError:

    def parse_iso(ts: str) -> datetime:  # type: ignore[misc]
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def format_display(dt: datetime) -> str:  # type: ignore[misc]
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _local_date(ts: str) -> str | None:
    if not ts:
        return None
    try:
        return parse_iso(ts).astimezone().strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def render_event(event: dict) -> str:
    """
    Render one CC JSONL event dict to a markdown block.
    Returns '' for uninteresting types (empty content, unknown type, etc.).
    """
    mtype = event.get("type")
    ts_raw = event.get("timestamp", "")
    try:
        ts = format_display(parse_iso(ts_raw)) if ts_raw else ""
    except (ValueError, TypeError):
        ts = ts_raw

    if mtype == "user":
        content = event.get("message", {}).get("content", "")
        if isinstance(content, list):
            parts = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    parts.append(c.get("text", ""))
                elif c.get("type") == "tool_result":
                    tc = c.get("content", "")
                    if isinstance(tc, list):
                        tc = " ".join(
                            p.get("text", "") for p in tc if isinstance(p, dict)
                        )
                    elide = str(tc)[:200].replace("\n", " ")
                    parts.append(
                        f"_[tool result: {elide}{'...' if len(str(tc)) > 200 else ''}]_"
                    )
            content = "\n".join(p for p in parts if p)
        if not content:
            return ""
        return f"\n### User — {ts}\n\n{content}\n"

    if mtype == "assistant":
        body = event.get("message", {})
        content = body.get("content", "")
        if isinstance(content, list):
            parts = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text":
                    parts.append(c.get("text", ""))
                elif c.get("type") == "tool_use":
                    tname = c.get("name", "?")
                    tin = c.get("input", {})
                    raw = json.dumps(tin)
                    summary = raw[:200].replace("\n", " ")
                    parts.append(
                        f"_[tool: {tname}({summary}{'...' if len(raw) > 200 else ''})]_"
                    )
            content = "\n".join(p for p in parts if p)
        elif not isinstance(content, str):
            return ""
        if not content:
            return ""
        return f"\n### Assistant — {ts}\n\n{content}\n"

    return ""


def _render_day(date_str: str, session_map: dict[str, list[str]]) -> str:
    parts = [
        f"# Chat log — {date_str}\n",
        f"\n_rendered {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_\n",
    ]
    for session_id, blocks in sorted(session_map.items()):
        parts.append(f"\n---\n\n## Session {session_id}\n")
        parts.extend(blocks)
        parts.append(f"\n_({len(blocks)} messages rendered for this day)_\n")
    return "".join(parts)


class CCEventFormatter(logging.Formatter):
    """
    Formats a CC JSONL event dict (carried in record.msg) to markdown.
    Falls back to standard Formatter for non-dict records.
    """

    def format(self, record: logging.LogRecord) -> str:
        if isinstance(record.msg, dict):
            return render_event(record.msg)
        return super().format(record)


class ChatLogHandler(logging.Handler):
    """
    Accumulates CC chat events and writes date-partitioned markdown files.

    Events are grouped by (local_date, session_id). flush() rebuilds each
    touched day file as the union of all buffered session contributions.

    Set record.session_id on each LogRecord to control session grouping;
    defaults to "unknown".
    """

    def __init__(self, output_dir: Path | None = None) -> None:
        super().__init__()
        if output_dir is None:
            from unseen_university.config.device_config import unseen_university_logs

            output_dir = unseen_university_logs() / "CC.0"
        self._output_dir = Path(output_dir)
        self.setFormatter(CCEventFormatter())
        self._buffer: dict[str, dict[str, list[str]]] = {}

    def emit(self, record: logging.LogRecord) -> None:
        event = record.msg
        if not isinstance(event, dict):
            return
        date = _local_date(event.get("timestamp", ""))
        if date is None:
            return
        try:
            rendered = self.format(record)
        except Exception:
            self.handleError(record)
            return
        if not rendered:
            return
        session_id = getattr(record, "session_id", "") or "unknown"
        self._buffer.setdefault(date, {}).setdefault(session_id, []).append(rendered)

    def flush(self) -> None:
        """Write all buffered events to their day files and clear the buffer."""
        if not self._buffer:
            return
        self._output_dir.mkdir(parents=True, exist_ok=True)
        for date, session_map in sorted(self._buffer.items()):
            (self._output_dir / f"{date}.md").write_text(_render_day(date, session_map))
        self._buffer.clear()


def ingest_session(
    path: Path,
    handler: ChatLogHandler,
    session_id: str | None = None,
) -> int:
    """
    Read a JSONL transcript and emit all events through a ChatLogHandler.

    Returns the count of records handed to the handler (including those that
    rendered to empty strings — the handler discards those silently).
    session_id defaults to the file stem when omitted.
    """
    sid = session_id or path.stem
    count = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or not event.get("timestamp"):
                continue
            record = logging.LogRecord(
                name="CC.0.chat",
                level=logging.INFO,
                pathname=str(path),
                lineno=0,
                msg=event,
                args=(),
                exc_info=None,
            )
            record.session_id = sid  # type: ignore[attr-defined]
            handler.handle(record)
            count += 1
    return count

"""palace_metric.py — palace-backed counter metrics + ASCII sparkline.

Pattern for slow progress metrics stored in `clan.memory_palace`:
  - Counter node: content is a single integer, updated by increment_metric().
  - History node: content is newline-delimited `YYYY-MM-DD HH:MM | key:N key:N`
    rows, appended by append_history().
  - Sparkline: render_sparkline() reads a history node, extracts one key's
    values, returns an ASCII dot-line.

First consumer: theigors/metrics/approach_frame_audit/* (T-approach-frame-sensor-node).
Extended by T-slow-metrics-sensor-tree-pattern to migrate other slow metrics
into the same shape.

This complements sensor_tree.py (event-triggered push sources) by covering
the other monitoring shape — cumulative progress counters with history.

revision: 2026-04-21 — initial (T-approach-frame-sensor-node)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_HISTORY_ROW_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})\s*\|\s*(?P<payload>.+)$"
)


def _get_content(cur, path: str) -> str:
    cur.execute(
        "SELECT content FROM memory_palace WHERE path = %s",
        (path,),
    )
    row = cur.fetchone()
    if row is None:
        raise KeyError(f"palace node not found: {path}")
    return row[0]


def _set_content(cur, path: str, content: str, actor: str) -> None:
    cur.execute(
        """
        UPDATE memory_palace
        SET content = %s, updated_at = %s, updated_by = %s
        WHERE path = %s
        """,
        (content, datetime.now(timezone.utc).strftime("%Y-%m-%d"), actor, path),
    )


def read_counter(cur, metric_path: str) -> int:
    content = _get_content(cur, metric_path).strip()
    if not content:
        return 0
    try:
        return int(content.split()[0])
    except ValueError as exc:
        raise ValueError(
            f"counter at {metric_path} is not an int: {content!r}"
        ) from exc


def increment_metric(
    cur, metric_path: str, by: int = 1, actor: str = "palace_metric"
) -> int:
    current = read_counter(cur, metric_path)
    new = current + by
    _set_content(cur, metric_path, str(new), actor)
    return new


def append_history(
    cur,
    history_path: str,
    row_payload: str,
    actor: str = "palace_metric",
    ts: datetime | None = None,
) -> str:
    """Append a row `YYYY-MM-DD HH:MM | <payload>` to the history node."""
    if ts is None:
        ts = datetime.now(timezone.utc)
    stamp = ts.strftime("%Y-%m-%d %H:%M")
    row = f"{stamp} | {row_payload}"
    existing = _get_content(cur, history_path).rstrip()
    new_content = (existing + "\n" + row).strip() + "\n"
    _set_content(cur, history_path, new_content, actor)
    return row


def parse_history(content: str, key: str) -> list[int]:
    """Extract int values for `key:<int>` from history rows in order."""
    values: list[int] = []
    for line in content.splitlines():
        m = _HISTORY_ROW_RE.match(line.strip())
        if not m:
            continue
        payload = m.group("payload")
        for token in payload.replace(",", " ").split():
            if ":" not in token:
                continue
            k, v = token.split(":", 1)
            if k == key:
                try:
                    values.append(int(v))
                except ValueError as e:
                    log.debug("extract_history: int(v) failed: %s", e)
                break
    return values


_SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def render_sparkline(values: list[int], width: int = 40) -> str:
    """Render a list of ints as an 8-level ASCII sparkline. Tail-truncated to width."""
    if not values:
        return ""
    tail = values[-width:]
    vmax = max(tail)
    vmin = min(tail)
    span = vmax - vmin
    if span == 0:
        return _SPARK_CHARS[4] * len(tail)
    out = []
    for v in tail:
        idx = round((v - vmin) / span * (len(_SPARK_CHARS) - 1))
        out.append(_SPARK_CHARS[idx])
    return "".join(out)


def render_history_sparkline(cur, history_path: str, key: str, width: int = 40) -> str:
    """Convenience: read history node, extract `key:` values, render sparkline."""
    content = _get_content(cur, history_path)
    values = parse_history(content, key)
    return render_sparkline(values, width=width)

"""
Metrics tool — Igor can call get_metrics_report to examine his own internals.
Also provides get_error_log to review recent runtime errors.
"""

from .registry import Tool, registry


def _get_metrics_report(cortex_db_path: str = "", **_) -> str:
    """
    Generate a full internal metrics report.
    Igor calls this to understand his own performance and routing patterns.
    """
    try:
        from ..cognition.metrics import build_report

        cortex = None
        if not cortex_db_path:
            import os

            cortex_db_path = os.getenv("IGOR_DB_PATH", "")
        if cortex_db_path:
            from pathlib import Path
            from ..memory.cortex import Cortex

            cortex = Cortex(Path(cortex_db_path))
        return build_report(cortex=cortex)
    except Exception as e:
        return f"Error generating metrics: {e}"


def _get_error_log(lines: int = 50, **_) -> str:
    """Return the most recent entries from errors.log."""
    try:
        from pathlib import Path

        log_path = Path.home() / ".TheIgors" / "logs" / "errors.log"
        if not log_path.exists():
            return "errors.log does not exist yet — no errors have been recorded."
        entries = log_path.read_text(encoding="utf-8").splitlines()
        if not entries:
            return "errors.log is empty."
        shown = entries[:lines]
        header = (
            f"errors.log — {len(entries)} total entries, showing latest {len(shown)}:\n"
        )
        return header + "\n".join(shown)
    except Exception as e:
        return f"Error reading error log: {e}"


registry.register(
    Tool(
        name="get_error_log",
        description=(
            "Read recent runtime errors from errors.log. "
            "Captures: impulse skips (local too slow), tier failures (tier.3/3.5/4/5), "
            "and other degraded-mode events. "
            "Use when Akien or Claude Code asks you to check the error log."
        ),
        parameters={
            "type": "object",
            "properties": {
                "lines": {
                    "type": "integer",
                    "description": "Number of recent error entries to return (default 50).",
                }
            },
            "required": [],
        },
        fn=_get_error_log,
    )
)


def _get_slow_query_report(top: int = 10, min_ms: int = 50, **_) -> str:
    """
    Parse db_queries.log and return top slow query patterns with p50/p95/p99 stats.
    Groups by normalized SQL prefix (first 80 chars, whitespace collapsed).
    """
    import re
    import statistics
    from pathlib import Path
    from collections import defaultdict

    log_path = Path.home() / ".TheIgors" / "logs" / "db_queries.log"
    if not log_path.exists():
        return "db_queries.log does not exist — no slow queries recorded yet."

    pattern_re = re.compile(r"elapsed=(\d+)ms\s+sql=(.*)")
    buckets: dict[str, list[int]] = defaultdict(list)

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return f"Error reading db_queries.log: {e}"

    for line in lines:
        m = pattern_re.search(line)
        if not m:
            continue
        elapsed = int(m.group(1))
        if elapsed < min_ms:
            continue
        sql_raw = m.group(2).strip()
        # Normalize: collapse whitespace, take first 80 chars as pattern key
        sql_norm = " ".join(sql_raw.split())[:80]
        buckets[sql_norm].append(elapsed)

    if not buckets:
        return f"No queries ≥{min_ms}ms found in db_queries.log ({len(lines)} lines scanned)."

    def pct(vals, p):
        vals_s = sorted(vals)
        idx = max(0, int(len(vals_s) * p / 100) - 1)
        return vals_s[idx]

    rows = []
    for sql, times in buckets.items():
        rows.append(
            {
                "sql": sql,
                "count": len(times),
                "p50": pct(times, 50),
                "p95": pct(times, 95),
                "p99": pct(times, 99),
                "max": max(times),
                "total": sum(times),
            }
        )

    rows.sort(key=lambda r: r["p95"], reverse=True)
    rows = rows[:top]

    lines_out = [
        f"db_queries.log — top {len(rows)} patterns ≥{min_ms}ms "
        f"({len(lines)} lines scanned):\n"
    ]
    for i, r in enumerate(rows, 1):
        lines_out.append(
            f"{i:2}. [{r['count']:4}x] p50={r['p50']}ms p95={r['p95']}ms "
            f"p99={r['p99']}ms max={r['max']}ms total={r['total']}ms"
        )
        lines_out.append(f"    {r['sql']}")

    return "\n".join(lines_out)


registry.register(
    Tool(
        name="get_slow_query_report",
        description=(
            "Parse db_queries.log and return the top slow SQL query patterns with "
            "p50/p95/p99/max/count stats. Groups by normalized SQL prefix. "
            "Use when asked to check database performance, slow queries, or what's "
            "making Igor slow. Parameters: top (default 10), min_ms (default 50)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "top": {
                    "type": "integer",
                    "description": "Number of top patterns to return (default 10).",
                },
                "min_ms": {
                    "type": "integer",
                    "description": "Minimum elapsed ms to include (default 50).",
                },
            },
            "required": [],
        },
        fn=_get_slow_query_report,
    )
)


registry.register(
    Tool(
        name="get_metrics_report",
        description=(
            "Generate a full internal metrics report showing tier distribution, "
            "escalation rate, word graph stats, memory counts, and top tools. "
            "Use this to understand your own performance, routing patterns, and "
            "what's working vs what needs improvement."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_get_metrics_report,
    )
)

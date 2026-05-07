"""
Slow query analysis tool.

Parses ~/.TheIgors/logs/db_queries.log, groups by normalized SQL pattern,
and surfaces the top offenders by frequency and by worst elapsed time.

Registered tool: analyze_slow_queries()
"""

import logging
import re
import os
from collections import Counter, defaultdict
from pathlib import Path

from .registry import Tool, registry
from ..paths import paths

log = logging.getLogger(__name__)

_LOG_PATH = paths().logs / "db_queries.log"
_DEFAULT_TOP_N = 10


def _normalize(sql: str) -> str:
    """Strip variable parts to get a stable pattern key."""
    s = re.sub(r"'[^']{4,}'", "'?'", sql)  # string literals
    s = re.sub(r"\b\d{4,}\b", "?", s)  # long numbers
    s = re.sub(r"%s", "?", s)  # postgres placeholders
    s = re.sub(r"\s+", " ", s).strip()
    return s[:100]


def analyze_slow_queries(top_n: str = "10") -> str:
    """
    Parse db_queries.log and return the top slow query patterns by frequency
    and by worst elapsed time. Accepts top_n as a string (auto-dispatch compat).
    """
    n = int(top_n) if str(top_n).isdigit() else _DEFAULT_TOP_N

    if not _LOG_PATH.exists():
        return "db_queries.log not found — no slow queries recorded yet."

    freq: Counter = Counter()
    total_ms: defaultdict = defaultdict(int)
    worst_ms: defaultdict = defaultdict(int)
    count: defaultdict = defaultdict(int)
    examples: dict = {}

    for line in _LOG_PATH.read_text(errors="replace").splitlines():
        m = re.match(r".* elapsed=(\d+)ms sql=(.+)", line)
        if not m:
            continue
        ms, sql = int(m.group(1)), m.group(2)
        key = _normalize(sql)
        freq[key] += 1
        total_ms[key] += ms
        count[key] += 1
        if ms > worst_ms[key]:
            worst_ms[key] = ms
            examples[key] = sql[:120]

    if not freq:
        return "db_queries.log exists but contains no parseable entries."

    lines = [f"Slow query report — {sum(freq.values())} total slow entries\n"]

    lines.append(f"TOP {n} BY FREQUENCY:")
    for key, hits in freq.most_common(n):
        avg = total_ms[key] // count[key]
        lines.append(f"  {hits:5d}x  avg={avg}ms  worst={worst_ms[key]}ms")
        lines.append(f"          {key}")

    lines.append(f"\nTOP {n} BY WORST SINGLE ELAPSED:")
    for key, ms in sorted(worst_ms.items(), key=lambda x: -x[1])[:n]:
        lines.append(f"  {ms:6d}ms  {freq[key]}x  {key}")

    lines.append("\nACTION CANDIDATES (>500ms worst or >100 hits):")
    for key in freq:
        if worst_ms[key] > 500 or freq[key] > 100:
            avg = total_ms[key] // count[key]
            lines.append(
                f"  worst={worst_ms[key]}ms hits={freq[key]} avg={avg}ms  {key[:80]}"
            )

    return "\n".join(lines)


def boot_surface_slow_queries(cortex, top_n: int = 5) -> None:
    """T-slow-query-boot-surface: push a compact slow-query report to ring_memory at boot.

    Keeps the full analysis on-disk in db_queries.log; surfaces the top-N worst
    offenders to ring as a single SLOW_QUERY_REPORT entry. Before this, the
    analyzer existed (analyze_slow_queries tool) but findings were never
    proactively visible — only on-demand.

    Silent on any failure — boot must not block on diagnostics.
    """
    try:
        if not _LOG_PATH.exists():
            return
        freq: Counter = Counter()
        worst_ms: defaultdict = defaultdict(int)
        count: defaultdict = defaultdict(int)
        total_ms: defaultdict = defaultdict(int)
        for line in _LOG_PATH.read_text(errors="replace").splitlines():
            m = re.match(r".* elapsed=(\d+)ms sql=(.+)", line)
            if not m:
                continue
            ms, sql = int(m.group(1)), m.group(2)
            key = _normalize(sql)
            freq[key] += 1
            count[key] += 1
            total_ms[key] += ms
            if ms > worst_ms[key]:
                worst_ms[key] = ms
        if not freq:
            return
        top = sorted(worst_ms.items(), key=lambda x: -x[1])[:top_n]
        lines = [f"SLOW_QUERY_REPORT|total={sum(freq.values())}|top{top_n}_by_worst:"]
        for key, ms in top:
            avg = total_ms[key] // max(count[key], 1)
            lines.append(
                f"  {ms:5d}ms worst  {freq[key]}x hits  avg={avg}ms  {key[:70]}"
            )
        cortex.write_ring("\n".join(lines), category="db_diagnostic")
    except Exception as e:
        log.debug("analyze_slow_queries: cortex.write_ring failed: %s", e)


registry.register(
    Tool(
        name="analyze_slow_queries",
        description=(
            "Parse db_queries.log and report the top slow SQL patterns by frequency "
            "and by worst elapsed time. Helps identify which queries to optimize. "
            "Optional: top_n (default 10)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "string",
                    "description": "How many top results to show (default 10)",
                }
            },
            "required": [],
        },
        fn=analyze_slow_queries,
    )
)

"""
Metrics tool — Igor can call get_metrics_report to examine his own internals.
Also provides get_error_log to review recent runtime errors.
Also provides get_milieu_state to read current affective state.
"""

import time

from .registry import Tool, registry

# ── Milieu helpers ────────────────────────────────────────────────────────────


def _emotion_label(valence: float, arousal: float, dominance: float) -> str:
    """Derive a human-readable dominant emotion label from VAD coordinates."""
    if abs(valence) < 0.1 and abs(arousal) < 0.1:
        return "neutral"
    if valence >= 0.2 and arousal >= 0.2:
        return "engaged/excited"
    if valence >= 0.2 and arousal < -0.1:
        return "content/calm"
    if valence >= 0.1 and abs(arousal) < 0.15:
        return "positive/steady"
    if valence <= -0.2 and arousal >= 0.2:
        if dominance < 0.0:
            return "stressed/overwhelmed"
        return "anxious/activated"
    if valence <= -0.2 and arousal < -0.1:
        return "subdued/depressed"
    if valence <= -0.1 and abs(arousal) < 0.15:
        return "mildly negative"
    if abs(valence) < 0.15 and arousal >= 0.2:
        if dominance > 0.2:
            return "alert/focused"
        return "restless/unsettled"
    if abs(valence) < 0.15 and arousal < -0.1:
        return "tired/inactive"
    return "mixed"


def _get_milieu_state(**_) -> str:
    """Return current milieu state: valence, arousal, dominance, emotion label, gradients."""
    try:
        from ..cognition.milieu import get as _get_milieu_singleton

        milieu = _get_milieu_singleton()
        if milieu is None:
            return "Milieu not initialized — Igor may not be fully booted."

        s = milieu.get_state()
        aro_grad = milieu.gradient("arousal")
        val_grad = milieu.gradient("valence")
        emotion = _emotion_label(s.valence, s.arousal, s.dominance)

        hist = milieu.session_histogram()
        session_char = hist.get("session_character", "unknown")
        sample_count = hist.get("sample_count", 0)

        age_s = int(time.time() - s.last_update) if s.last_update > 0 else None
        age_str = f"{age_s}s ago" if age_s is not None else "unknown"

        lines = [
            f"MILIEU STATE (tick={s.tick}, last_update={age_str}):",
            f"  valence   = {s.valence:+.3f}  (gradient {val_grad:+.4f})",
            f"  arousal   = {s.arousal:+.3f}  (gradient {aro_grad:+.4f})",
            f"  dominance = {s.dominance:+.3f}",
            f"  emotion   = {emotion}",
            f"  session   = {session_char} ({sample_count} samples this session)",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading milieu state: {e}"


registry.register(
    Tool(
        name="get_milieu_state",
        description=(
            "Return current affective state: valence, arousal, dominance, "
            "dominant emotion label, per-dim gradients (rising/falling), and "
            "session character (bouncy/stressed/focused/calm/neutral). "
            "Use to inspect your own emotional register — check mood, notice if "
            "arousal is climbing, or report your current state to Akien."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_get_milieu_state,
    )
)


def _get_metrics_report(cortex_db_path: str = "", **_) -> str:
    """
    Generate a full internal metrics report.
    Igor calls this to understand his own performance and routing patterns.
    """
    try:
        from ..cognition.metrics import build_report

        from ..memory.cortex import Cortex

        cortex = Cortex(None)
        return build_report(cortex=cortex)
    except Exception as e:
        return f"Error generating metrics: {e}"


def _get_error_log(lines: int = 50, **_) -> str:
    """Return the most recent entries from errors.log."""
    try:
        from pathlib import Path

        from ..paths import paths as _paths

        log_path = _paths().logs / "errors.log"
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

    from ..paths import paths as _paths

    log_path = _paths().logs / "db_queries.log"
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


def _get_tool_registry_report(top: int = 20, **_) -> str:
    """
    Return per-tool call count, error rate, p50/p95 latency from the live ToolRegistry.
    Sorted by call count descending. Shows top N tools.
    """
    from .registry import registry as _registry

    stats = _registry.tool_stats()
    if not stats:
        return "No tool calls recorded yet this session."

    lines = [f"TOOL REGISTRY — {len(stats)} tools called this session (top {top}):\n"]
    for tool_name, d in list(stats.items())[:top]:
        p50 = f"{d['p50_ms']}ms" if d["p50_ms"] is not None else "—"
        p95 = f"{d['p95_ms']}ms" if d["p95_ms"] is not None else "—"
        err_pct = f"{d['error_rate']:.0%}" if d["errors"] > 0 else "0%"
        lines.append(
            f"  {tool_name:<36} {d['calls']:4}x  err={err_pct}  p50={p50}  p95={p95}"
        )
    return "\n".join(lines)


registry.register(
    Tool(
        name="get_tool_registry_report",
        description=(
            "Return per-tool call statistics for this session: call count, error rate, "
            "p50/p95 latency. Sorted by call count. "
            "Use to understand which tools are called most, which fail, and which are slow. "
            "Feeds /audit thread-hygiene check."
        ),
        parameters={
            "type": "object",
            "properties": {
                "top": {
                    "type": "integer",
                    "description": "Max tools to show (default 20).",
                }
            },
            "required": [],
        },
        fn=_get_tool_registry_report,
    )
)


def _get_daemon_report(**_) -> str:
    """Return DaemonSupervisor status: all registered threads, alive/dead, uptime."""
    try:
        from ..cognition.daemon_supervisor import supervisor as _sup

        return _sup.report_str()
    except Exception as e:
        return f"Error reading daemon supervisor: {e}"


registry.register(
    Tool(
        name="get_daemon_report",
        description=(
            "Return the live status of all Igor daemon threads: name, alive/dead, "
            "uptime in seconds, and health check result (if defined). "
            "Use to check for dead threads, thread leaks, or as part of /audit. "
            "Covers: network-listener, discord-bot, web-server, boot-check, "
            "ne-worker, consolidation-worker, distillation-worker, ne-deep-consolidation."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_get_daemon_report,
    )
)


def _get_network_proxy_report(**_) -> str:
    """Return NetworkProxy per-host call stats: call count, error rate, p50/p95 latency."""
    try:
        from ..network.system_proxy import system_proxy as _sp

        return _sp.network.report_str() if _sp.network else "NetworkProxy not available"
    except Exception as e:
        return f"Error reading network proxy: {e}"


registry.register(
    Tool(
        name="get_network_proxy_report",
        description=(
            "Return per-host outbound HTTP call statistics tracked by NetworkProxy: "
            "call count, error rate, p50/p95 latency. "
            "Use to check external endpoint health or as part of /audit. "
            "Shows hosts called via proxy.get() / proxy.post() / proxy.post_json()."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_get_network_proxy_report,
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

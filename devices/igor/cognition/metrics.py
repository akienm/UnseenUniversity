"""
Internal metrics reporter.

Aggregates data from forensic logs, word graph, cortex, and session state
into a single human-readable report.

Used by:
  /metrics command in main.py
  get_metrics_report tool (Igor can call this to examine his own internals)
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from pathlib import Path

_LOGS_DIR = Path.home() / ".TheIgors" / "logs"


def _read_log_tail(name: str, n: int = 200) -> list[str]:
    path = _LOGS_DIR / name
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[:n]  # logs are newest-first
    except Exception:
        return []


def _tier_distribution(n: int = 100) -> dict[str, int]:
    """Count tier selections from reasoning_calls.log (newest-first)."""
    counts: Counter = Counter()
    lines = _read_log_tail("reasoning_calls.log", n * 2)
    seen = 0
    for line in lines:
        m = re.search(r"tier_select\|.*selected=(tier\.\d+)", line)
        if m:
            counts[m.group(1)] += 1
            seen += 1
            if seen >= n:
                break
    return dict(counts)


def _escalation_rate() -> tuple[float, int, int]:
    """Latest escalation_rate entry → (rate, cloud_calls, total_calls)."""
    for line in _read_log_tail("cognition_metrics.log", 50):
        if "|escalation_rate|" in line:
            parts = line.split("|")
            try:
                rate = float(parts[2])
                cloud = int(re.search(r"cloud=(\d+)", line).group(1))
                total = int(re.search(r"total=(\d+)", line).group(1))
                return rate, cloud, total
            except Exception:
                pass
    return 0.0, 0, 0


def _top_tools(n: int = 8) -> list[tuple[str, int]]:
    """Most-used tools from tool_calls.log."""
    counts: Counter = Counter()
    for line in _read_log_tail("tool_calls.log", 300):
        m = re.search(r"\|tool\|OK\|([^\|]+)\|", line)
        if m:
            counts[m.group(1)] += 1
    return counts.most_common(n)


def _winnow_stats(n: int = 200) -> tuple[int, int]:
    """Count winnow fires and total memories added from tool_calls.log.
    Returns (fires, memories_added) — approximate from search calls near winnow."""
    # Winnow fires appear as reasoning calls with context_winnow in logs
    # Best proxy: count cortex.search calls that happen before reasoning calls
    fires = 0
    for line in _read_log_tail("reasoning_calls.log", n):
        if "winnow" in line.lower():
            fires += 1
    return fires, 0  # memories_added tracking requires more instrumentation


def _word_graph_stats():
    """Get live word graph stats from basal_ganglia."""
    try:
        from .basal_ganglia import _word_graph as wg
        if wg is None:
            return None
        vocab = len(wg._word_to_ids)
        docs = wg._doc_count
        hubs = wg.top_hubs(n=5)
        return {"vocab": vocab, "docs": docs, "hubs": hubs}
    except Exception:
        return None


def build_report(cortex=None, session_interactions: int = 0,
                 session_cost: float = 0.0, upstream_calls: int = 0) -> str:
    """
    Build the full metrics report. Returns a formatted string.
    All parameters are optional — provides whatever data is available.
    """
    lines = ["═" * 54, "  Igor Internal Metrics", "═" * 54, ""]

    # ── Session ───────────────────────────────────────────────
    lines.append("SESSION")
    lines.append(f"  Interactions:      {session_interactions}")
    lines.append(f"  Cost:              ${session_cost:.4f}")
    if session_interactions > 0:
        upstream_pct = round(upstream_calls / max(session_interactions, 1) * 100)
        lines.append(f"  Upstream calls:    {upstream_calls} ({upstream_pct}%)")
    lines.append("")

    # ── Tier distribution ─────────────────────────────────────
    tier_counts = _tier_distribution(n=100)
    total_tiers = sum(tier_counts.values())
    local_calls = tier_counts.get("tier.1", 0) + tier_counts.get("tier.2", 0)
    local_pct = round(local_calls / max(total_tiers, 1) * 100)
    lines.append(f"TIER DISTRIBUTION  (last 100)   LOCAL: {local_pct}%  CLOUD: {100 - local_pct}%")
    for tier in ["tier.1", "tier.2", "tier.3", "tier.3.5", "tier.4", "tier.5"]:
        count = tier_counts.get(tier, 0)
        if count == 0 and tier not in ("tier.1", "tier.2", "tier.3", "tier.4"):
            continue
        pct = round(count / max(total_tiers, 1) * 100)
        label = {
            "tier.1": "habit (local)",
            "tier.2": "Ollama (local)",
            "tier.3": "cheap OR (cloud)",
            "tier.3.5": "haiku (cloud)",
            "tier.4": "sonnet (cloud)",
            "tier.5": "Anthropic direct",
        }.get(tier, tier)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        lines.append(f"  {tier}  {bar}  {count:3d} ({pct:2d}%)  {label}")
    lines.append("")

    # ── Escalation ────────────────────────────────────────────
    esc_rate, cloud, total = _escalation_rate()
    lines.append("ESCALATION  (lifetime)")
    lines.append(f"  Rate:              {esc_rate:.1%}  ({cloud} cloud / {total} total)")
    lines.append("")

    # ── Word graph ────────────────────────────────────────────
    wg = _word_graph_stats()
    lines.append("WORD GRAPH")
    if wg:
        lines.append(f"  Vocabulary:        {wg['vocab']:,} words")
        lines.append(f"  Docs indexed:      {wg['docs']}")
        if wg["hubs"]:
            hub_str = "  ".join(f"{w}({c})" for w, c in wg["hubs"])
            lines.append(f"  Top hubs:          {hub_str}")
    else:
        lines.append("  (word graph not available)")
    lines.append("")

    # ── Memory ────────────────────────────────────────────────
    lines.append("MEMORY")
    if cortex is not None:
        try:
            total_mem = cortex.total_count()
            counts = cortex.count_by_type()
            habits = cortex.get_habits()
            lines.append(f"  Total memories:    {total_mem}")
            lines.append(f"  Habits:            {len(habits)}")
            from ..memory.models import MemoryType
            ep = counts.get(MemoryType.EPISODIC.value, 0)
            interp = counts.get(MemoryType.INTERPRETIVE.value, 0)
            exp = counts.get(MemoryType.EXPERIENTIAL.value, 0)
            lines.append(f"  Episodic:          {ep}  Interpretive: {interp}  Experiential: {exp}")
        except Exception:
            lines.append("  (cortex unavailable)")
    else:
        lines.append("  (cortex not provided)")
    lines.append("")

    # ── Top tools ─────────────────────────────────────────────
    top_tools = _top_tools(n=8)
    if top_tools:
        lines.append("TOP TOOLS  (lifetime)")
        for tool_name, count in top_tools:
            lines.append(f"  {tool_name:<36} {count:4d}x")
        lines.append("")

    lines.append("═" * 54)
    return "\n".join(lines)

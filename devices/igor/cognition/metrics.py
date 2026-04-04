"""
Internal metrics reporter.

Aggregates data from forensic logs, word graph, cortex, and session state
into a single human-readable report.

Used by:
  /metrics command in main.py
  get_metrics_report tool (Igor can call this to examine his own internals)
"""

from __future__ import annotations
import logging

import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..paths import paths

_LOGS_DIR = paths().logs


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
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/cognition/metrics.py: %s", _bare_e
                )
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


def _context_stats(n: int = 50) -> dict:
    """
    G55: Parse context_chars and token counts from recent reasoning_calls.log entries.
    Returns per-tier averages: {tier: {avg_ctx_chars, avg_in, avg_out, calls}}.
    """
    import re as _re

    stats: dict[str, dict] = {}
    lines = _read_log_tail("reasoning_calls.log", n * 3)
    seen = 0
    for line in lines:
        if "|reasoning|" not in line:
            continue
        tier_m = _re.search(r"\|tier=(tier\.\S+?)\|", line)
        ctx_m = _re.search(r"\|ctx=(\d+)", line)
        in_m = _re.search(r"\|in=(\d+)", line)
        out_m = _re.search(r"\|out=(\d+)", line)
        tier = tier_m.group(1) if tier_m else "unknown"
        ctx = int(ctx_m.group(1)) if ctx_m else 0
        in_t = int(in_m.group(1)) if in_m else 0
        out_t = int(out_m.group(1)) if out_m else 0
        if tier not in stats:
            stats[tier] = {"ctx_total": 0, "in_total": 0, "out_total": 0, "calls": 0}
        stats[tier]["ctx_total"] += ctx
        stats[tier]["in_total"] += in_t
        stats[tier]["out_total"] += out_t
        stats[tier]["calls"] += 1
        seen += 1
        if seen >= n:
            break
    result = {}
    for tier, d in stats.items():
        c = max(d["calls"], 1)
        result[tier] = {
            "avg_ctx_chars": d["ctx_total"] // c,
            "avg_in": d["in_total"] // c,
            "avg_out": d["out_total"] // c,
            "calls": d["calls"],
        }
    return result


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


def _ne_stats(n: int = 100) -> dict:
    """
    #174: Parse ne_runs.log to summarise NE telemetry.
    Returns {runs, ok, failed, avg_obs, avg_promoted, avg_impulses, avg_elapsed_ms}.
    """
    lines = _read_log_tail("ne_runs.log", n)
    runs = ok = failed = 0
    obs_total = promoted_total = impulses_total = elapsed_total = 0
    for line in lines:
        if "|ne_run|" not in line:
            continue
        runs += 1
        if "|ne_run|SKIPPED|" in line:
            continue
        if "|ne_run|OK|" in line:
            ok += 1
            m_obs = re.search(r"\|obs=(\d+)", line)
            m_prom = re.search(r"\|promoted=(\d+)", line)
            m_imp = re.search(r"\|impulses=(\d+)", line)
            m_el = re.search(r"\|elapsed=(\d+)ms", line)
            obs_total += int(m_obs.group(1)) if m_obs else 0
            promoted_total += int(m_prom.group(1)) if m_prom else 0
            impulses_total += int(m_imp.group(1)) if m_imp else 0
            elapsed_total += int(m_el.group(1)) if m_el else 0
        else:
            failed += 1
    safe_ok = max(ok, 1)
    return {
        "runs": runs,
        "ok": ok,
        "failed": failed,
        "avg_obs": obs_total // safe_ok,
        "avg_promoted": promoted_total / safe_ok,
        "avg_impulses": impulses_total / safe_ok,
        "avg_elapsed_ms": elapsed_total // safe_ok,
    }


def _consolidation_stats(cortex) -> dict | None:
    """
    #174: Read consolidation run stats from ring_memory.
    Returns dict with last-run info, or None if no runs recorded.
    """
    if cortex is None:
        return None
    try:
        entries = cortex.read_ring_memory(limit=20, category="consolidation")
        if not entries:
            return None
        # Newest-first; find last CONSOLIDATION| entry
        for e in entries:
            content = e.get("content", "")
            if content.startswith("CONSOLIDATION|"):
                m_cl = re.search(r"clusters=(\d+)", content)
                m_ex = re.search(r"extracted=(\d+)", content)
                m_sk = re.search(r"skipped=(\d+)", content)
                ts = e.get("timestamp", "")[:16]
                return {
                    "last_run": ts,
                    "clusters": int(m_cl.group(1)) if m_cl else 0,
                    "extracted": int(m_ex.group(1)) if m_ex else 0,
                    "skipped": int(m_sk.group(1)) if m_sk else 0,
                }
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/cognition/metrics.py: %s", _bare_e
        )
    return None


def _self_training_stats(n_days: int = 7) -> dict:
    """
    Parse cognition_metrics.log for self_training_pass entries.
    cognition_metrics.log is append-only (oldest first), so we read the full
    file and scan all lines.

    Returns:
        last_run: ISO timestamp of last pass, or None
        last_scanned: int
        last_gaps: int
        last_deposited: int
        total_deposited: int  — all-time from deposited= fields
        gap_rate_7d: float   — avg gaps/scanned over last 7 days (0.0 if no scans)
        daily_gap_rates: list[tuple[str, float]]  — (date_str, rate) last 7 days
    """
    from datetime import datetime, timezone, timedelta

    path = _LOGS_DIR / "cognition_metrics.log"
    if not path.exists():
        return {}

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}

    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=n_days)

    last_run: str | None = None
    last_scanned = last_gaps = last_deposited = 0
    total_deposited = 0

    # daily accumulator: date_str → (scanned, gaps)
    daily: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))

    for line in lines:
        if "|self_training_pass|" not in line:
            continue
        ts_str = line.split("|", 1)[0]
        try:
            ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        m_sc = re.search(r"scanned=(\d+)", line)
        m_ga = re.search(r"gaps=(\d+)", line)
        m_de = re.search(r"deposited=(\d+)", line)
        scanned = int(m_sc.group(1)) if m_sc else 0
        gaps = int(m_ga.group(1)) if m_ga else 0
        deposited = int(m_de.group(1)) if m_de else 0

        total_deposited += deposited
        last_run = ts_str[:19]
        last_scanned = scanned
        last_gaps = gaps
        last_deposited = deposited

        if ts >= cutoff_7d and scanned > 0:
            date_key = ts_str[:10]
            prev_sc, prev_ga = daily[date_key]
            daily[date_key] = (prev_sc + scanned, prev_ga + gaps)

    # Compute per-day gap rates for the last 7 days
    daily_gap_rates: list[tuple[str, float]] = []
    for i in range(n_days - 1, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        sc, ga = daily.get(day, (0, 0))
        rate = ga / sc if sc > 0 else None
        if rate is not None:
            daily_gap_rates.append((day, round(rate, 3)))

    total_7d_scanned = sum(sc for sc, _ in daily.values())
    total_7d_gaps = sum(ga for _, ga in daily.values())
    gap_rate_7d = total_7d_gaps / total_7d_scanned if total_7d_scanned > 0 else 0.0

    return {
        "last_run": last_run,
        "last_scanned": last_scanned,
        "last_gaps": last_gaps,
        "last_deposited": last_deposited,
        "total_deposited": total_deposited,
        "gap_rate_7d": round(gap_rate_7d, 3),
        "daily_gap_rates": daily_gap_rates,
    }


def build_report(
    cortex=None,
    session_interactions: int = 0,
    session_cost: float = 0.0,
    cloud_calls: int = 0,
    ne=None,
) -> str:
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
        cloud_pct = round(cloud_calls / max(session_interactions, 1) * 100)
        lines.append(f"  Cloud inference:   {cloud_calls} ({cloud_pct}%)")
    lines.append("")

    # ── Tier distribution ─────────────────────────────────────
    tier_counts = _tier_distribution(n=100)
    total_tiers = sum(tier_counts.values())
    local_calls = tier_counts.get("tier.1", 0) + tier_counts.get("tier.2", 0)
    local_pct = round(local_calls / max(total_tiers, 1) * 100)
    lines.append(
        f"TIER DISTRIBUTION  (last 100)   LOCAL: {local_pct}%  CLOUD: {100 - local_pct}%"
    )
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

    # ── Layer boundary: context size per tier (G55) ───────────
    ctx_stats = _context_stats(n=50)
    if ctx_stats:
        lines.append("LAYER BOUNDARY  (last 50 inference calls)")
        _tier_order = ["tier.2", "tier.3", "tier.3.5", "tier.4", "tier.5", "unknown"]
        for tier in _tier_order:
            if tier not in ctx_stats:
                continue
            d = ctx_stats[tier]
            label = {
                "tier.2": "local Ollama",
                "tier.3": "cloud cheap",
                "tier.3.5": "cloud haiku",
                "tier.4": "cloud sonnet",
                "tier.5": "cloud direct",
            }.get(tier, tier)
            ctx_k = (
                f"{d['avg_ctx_chars'] // 1000}K"
                if d["avg_ctx_chars"] >= 1000
                else str(d["avg_ctx_chars"])
            )
            lines.append(
                f"  {tier:<9}  {d['calls']:3d} calls  "
                f"ctx≈{ctx_k}  in≈{d['avg_in']}  out≈{d['avg_out']}  ({label})"
            )
        lines.append("")

    # ── Escalation ────────────────────────────────────────────
    esc_rate, cloud, total = _escalation_rate()
    lines.append("ESCALATION  (lifetime)")
    lines.append(
        f"  Rate:              {esc_rate:.1%}  ({cloud} cloud / {total} total)"
    )
    lines.append("")

    # ── Response habituation (WO#140 Phase 2) ─────────────────
    try:
        from .response_habituation import ResponseHabituation as _RH

        _rh_path = paths().instance / "response_habituation.json"
        if _rh_path.exists():
            _rh = _RH(_rh_path)
            _top = _rh.top_habituated(n=5)
            lines.append("RESPONSE HABITUATION")
            lines.append(f"  Vocab tracked:     {_rh.vocab_size()} words")
            if _top:
                _top_str = "  ".join(f"{w}({c})" for w, c, _ in _top)
                lines.append(f"  Top 5 used:        {_top_str}")
            lines.append("")
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/cognition/metrics.py: %s", _bare_e
        )

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
            lines.append(
                f"  Episodic:          {ep}  Interpretive: {interp}  Experiential: {exp}"
            )
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

    # ── Narrative Engine (#174) ────────────────────────────────
    ne_s = _ne_stats(n=100)
    lines.append("NARRATIVE ENGINE  (last 100 log entries)")
    if ne_s["runs"] > 0:
        session_runs = getattr(ne, "_run_count", None)
        run_info = f"  session={session_runs}" if session_runs is not None else ""
        ok_pct = round(ne_s["ok"] / max(ne_s["runs"], 1) * 100)
        lines.append(
            f"  Log runs:          {ne_s['runs']}  OK={ne_s['ok']} ({ok_pct}%)  "
            f"failed={ne_s['failed']}{run_info}"
        )
        lines.append(
            f"  Avg obs/run:       {ne_s['avg_obs']}  "
            f"promoted={ne_s['avg_promoted']:.1f}  "
            f"impulses={ne_s['avg_impulses']:.1f}  "
            f"elapsed={ne_s['avg_elapsed_ms']}ms"
        )
    else:
        lines.append("  (no NE runs logged yet)")
    lines.append("")

    # ── Consolidation (#174) ───────────────────────────────────
    con_s = _consolidation_stats(cortex)
    lines.append("CONSOLIDATION")
    if con_s:
        lines.append(f"  Last run:          {con_s['last_run']}")
        lines.append(
            f"  Clusters:          {con_s['clusters']}  "
            f"extracted={con_s['extracted']}  skipped={con_s['skipped']}"
        )
    else:
        lines.append("  (no consolidation runs logged yet)")
    lines.append("")

    # ── Self-training (T-self-training-metrics) ────────────────
    st = _self_training_stats(n_days=7)
    lines.append("SELF-TRAINING  (last 7 days)")
    if st.get("last_run"):
        lines.append(f"  Last run:          {st['last_run']}")
        lines.append(
            f"  Last pass:         scanned={st['last_scanned']}  "
            f"gaps={st['last_gaps']}  deposited={st['last_deposited']}"
        )
        lines.append(f"  Total deposited:   {st['total_deposited']}  (all-time)")
        gap_rate = st["gap_rate_7d"]
        trend = (
            "→ densifying"
            if gap_rate < 0.1
            else ("→ learning" if gap_rate < 0.5 else "→ sparse")
        )
        lines.append(f"  7-day gap rate:    {gap_rate:.1%}  {trend}")
        if st["daily_gap_rates"]:
            daily_str = "  ".join(
                f"{d[5:]}:{r:.0%}" for d, r in st["daily_gap_rates"][-5:]
            )
            lines.append(f"  Daily gap rates:   {daily_str}")
    else:
        lines.append("  (no self-training runs logged yet)")
    lines.append("")

    lines.append("═" * 54)
    return "\n".join(lines)

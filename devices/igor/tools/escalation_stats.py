"""
escalation_stats.py — D279: Cloud escalation frequency by topic, this week vs last week.

Parses forensic logs to find cloud-tier calls (tier.3+), groups by topic keyword
extracted from the input snippet, and reports this-week vs prev-week trend.

Sources (in priority order):
  1. turn_trace.YYYYMMDD.log — rich JSON with full input + response.tier (last 2 days)
  2. escalation.log          — pipe-delimited per-turn records with tier + input snippet
                               (single file, not purged, but only cloud escalations)

Cloud tier definition: tier.3, tier.3.5, tier.4, tier.5 (anything above tier.2/Ollama).

Forensic log: ~/.TheIgors/logs/escalation_stats.log
"""

import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .registry import Tool, registry

from ..paths import paths as _paths
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_DB_URL = _paths().home_db_url


# Cloud tiers — anything above local Ollama (tier.2)
_CLOUD_TIER_RE = re.compile(r"tier\.(3|3\.5|4|5)$")

# Pipe-field extractor for escalation.log lines
_ESC_TIER_RE = re.compile(r"\|tier=([^|]+)")
_ESC_INPUT_RE = re.compile(r"\|input=(.+?)(?:\|[a-z_]+=|$)")
_ESC_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")






# ── Topic extraction ──────────────────────────────────────────────────────────

_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "i",
        "you",
        "your",
        "their",
        "he",
        "she",
        "it",
        "we",
        "they",
        "this",
        "that",
        "these",
        "those",
        "and",
        "or",
        "but",
        "not",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "from",
        "up",
        "cc",
        "igor",
        "routing",
        "directive",
        "respond",
        "inline",
        "what",
        "how",
        "please",
        "now",
        "just",
        "also",
        "so",
        "if",
        "then",
        "no",
        "yes",
    }
)


_THREAD_CTX_PREFIX_RE = re.compile(r"^\[Thread context[^\]]*\]\s*", re.IGNORECASE)
_TALKING_WITH_PREFIX_RE = re.compile(r"^TALKING WITH:[^\]]*\]:\s*", re.IGNORECASE)
_USER_LABEL_RE = re.compile(r"^\s*User:\s*", re.IGNORECASE)


def _topic_from_input(text: str, max_chars: int = 40) -> str:
    """
    Extract a normalised topic keyword from input text.
    - Strip boilerplate prefixes (thread context, TALKING WITH, etc.)
    - Lowercase, strip punctuation
    - Take first meaningful non-stop word (≥4 chars)
    - Fall back to first 40 chars of lowercased text
    """
    if not text:
        return "unknown"
    # Strip injected prefixes so they don't poison topic extraction
    stripped = _THREAD_CTX_PREFIX_RE.sub("", text)
    stripped = _TALKING_WITH_PREFIX_RE.sub("", stripped)
    # After stripping thread context, the text often starts "User: <message>  Igor: ..."
    # Extract just the first User turn
    user_m = _USER_LABEL_RE.match(stripped)
    if user_m:
        stripped = stripped[user_m.end() :]
    clean = re.sub(r"[^\w\s]", " ", stripped.lower())
    words = clean.split()
    for w in words:
        if len(w) >= 4 and w not in _STOP_WORDS:
            return w
    # Fall back: first 40 chars, whitespace-collapsed
    return " ".join(stripped.lower().split())[:max_chars].strip() or "unknown"




def _parse_turn_trace_logs(
    logs_dir: Path, since: datetime, until: datetime
) -> list[dict]:
    """
    Parse turn_trace.YYYYMMDD.log files for entries whose ts falls in [since, until).
    Returns list of {"ts": datetime, "topic": str, "tier": str}.
    """
    results = []
    for log_path in logs_dir.glob("turn_trace.*.log"):
        date_str = log_path.stem.split(".")[-1]
        if not date_str.isdigit() or len(date_str) != 8:
            continue
        try:
            log_date = datetime.strptime(date_str, "%Y%m%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        # Skip files entirely outside our window (quick filter)
        if log_date + timedelta(days=1) < since or log_date >= until:
            continue

        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.info(f"WARN  cannot read {log_path}: {e}")
            continue

        # Split on turn boundaries
        blocks = re.split(r"\n=== turn ", content)
        for block in blocks:
            # Find JSON blob
            json_start = block.find("{")
            if json_start == -1:
                continue
            json_end = block.rfind("}")
            if json_end == -1 or json_end <= json_start:
                continue
            try:
                ctx = json.loads(block[json_start : json_end + 1])
            except json.JSONDecodeError:
                continue

            ts_str = ctx.get("ts", "")
            response = ctx.get("response", {})
            tier = response.get("tier", "")
            input_text = ctx.get("input", "")

            if not ts_str or not tier:
                continue

            try:
                ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            if ts < since or ts >= until:
                continue

            m = _CLOUD_TIER_RE.match(tier)
            if not m:
                continue

            results.append(
                {
                    "ts": ts,
                    "topic": _topic_from_input(input_text),
                    "tier": tier,
                }
            )

    return results


def _parse_escalation_log(
    log_path: Path, since: datetime, until: datetime
) -> list[dict]:
    """
    Parse escalation.log (pipe-delimited, newest-first) for cloud-tier entries
    in [since, until). Returns list of {"ts": datetime, "topic": str, "tier": str}.
    """
    results = []
    if not log_path.exists():
        return results

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        log.info(f"WARN  cannot read escalation.log: {e}")
        return results

    for line in lines:
        if "|escalation|" not in line:
            continue

        ts_m = _ESC_TS_RE.match(line)
        if not ts_m:
            continue
        try:
            ts = datetime.fromisoformat(ts_m.group(1)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if ts < since or ts >= until:
            continue

        tier_m = _ESC_TIER_RE.search(line)
        if not tier_m:
            continue
        tier = tier_m.group(1)
        if not _CLOUD_TIER_RE.match(tier):
            continue

        input_m = _ESC_INPUT_RE.search(line)
        input_text = input_m.group(1).strip() if input_m else ""

        results.append(
            {
                "ts": ts,
                "topic": _topic_from_input(input_text),
                "tier": tier,
            }
        )

    return results




def _collect_cloud_calls(
    logs_dir: Path, since: datetime, until: datetime
) -> list[dict]:
    """
    Collect all cloud-tier calls in [since, until) from all available log sources.
    Deduplicate by (ts_minute, tier) to avoid double-counting if a turn appears
    in both turn_trace and escalation.log.
    """
    from_traces = _parse_turn_trace_logs(logs_dir, since, until)
    from_escalation = _parse_escalation_log(logs_dir / "escalation.log", since, until)

    # turn_trace is richer; use escalation only to fill in turns not covered by traces
    # Dedup key: timestamp truncated to minute + tier
    seen: set[tuple] = set()
    merged: list[dict] = []

    for entry in from_traces:
        key = (entry["ts"].strftime("%Y%m%dT%H%M"), entry["tier"])
        seen.add(key)
        merged.append(entry)

    for entry in from_escalation:
        key = (entry["ts"].strftime("%Y%m%dT%H%M"), entry["tier"])
        if key not in seen:
            seen.add(key)
            merged.append(entry)

    return merged


def _group_by_topic(entries: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for e in entries:
        counts[e["topic"]] += 1
    return dict(counts)


def _format_report(
    this_week: dict[str, int],
    prev_week: dict[str, int],
    now: datetime,
    top: int = 10,
) -> str:
    all_topics = set(this_week) | set(prev_week)
    if not all_topics:
        return "No cloud escalations found in the past 14 days."

    # Sort by this-week count descending, then prev-week descending
    ranked = sorted(
        all_topics,
        key=lambda t: (this_week.get(t, 0), prev_week.get(t, 0)),
        reverse=True,
    )[:top]

    this_total = sum(this_week.values())
    prev_total = sum(prev_week.values())

    lines = [
        f"CLOUD ESCALATION STATS — {now.strftime('%Y-%m-%d')}",
        f"  This week: {this_total} cloud calls   Prev week: {prev_total} cloud calls",
        "",
        f"{'TOPIC':<28} {'THIS WK':>8} {'PREV WK':>8} {'DELTA':>8}",
        "-" * 56,
    ]

    for topic in ranked:
        tw = this_week.get(topic, 0)
        pw = prev_week.get(topic, 0)
        delta = tw - pw
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        lines.append(f"  {topic:<26} {tw:>8} {pw:>8} {delta_str:>8}")

    return "\n".join(lines)


# ── Public function ───────────────────────────────────────────────────────────


def get_escalation_stats(**_) -> str:
    """
    D279: Report cloud escalation frequency by topic, this week vs last week.
    Returns a text summary suitable for posting to channel.
    """
    try:

        logs_dir = _paths().logs

        now = datetime.now(timezone.utc)
        this_week_start = now - timedelta(days=7)
        prev_week_start = now - timedelta(days=14)

        log.info(f"START  window={prev_week_start.date()}..{now.date()}")

        this_week_entries = _collect_cloud_calls(logs_dir, this_week_start, now)
        prev_week_entries = _collect_cloud_calls(
            logs_dir, prev_week_start, this_week_start
        )

        this_week_by_topic = _group_by_topic(this_week_entries)
        prev_week_by_topic = _group_by_topic(prev_week_entries)

        report = _format_report(this_week_by_topic, prev_week_by_topic, now)

        log.info(
            f"DONE  this_week={sum(this_week_by_topic.values())}"
            f"  prev_week={sum(prev_week_by_topic.values())}"
            f"  topics={len(set(this_week_by_topic) | set(prev_week_by_topic))}"
        )
        return report

    except Exception as e:
        msg = f"Error generating escalation stats: {e}"
        log.info(f"ERROR  {msg}")
        return msg


# ── Registry ──────────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="get_escalation_stats",
        description=(
            "D279: Report cloud escalation frequency by topic — this week vs last week. "
            "Parses turn_trace and escalation logs to count cloud-tier (tier.3+) calls, "
            "groups by topic keyword, and shows delta. "
            "Use to understand what questions are driving cloud spend and how it trends. "
            "Returns top 10 topics with this-week count, prev-week count, and delta."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=get_escalation_stats,
    )
)

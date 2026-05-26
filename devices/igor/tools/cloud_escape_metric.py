"""
cloud_escape_metric.py — T-cloud-escape-rate-metric

Generates a weekly cloud escape rate report bucketed by intent category.
Primary progress metric for D316 progressive autonomy path.

cloud_escape_rate_report(**_): scans turn_trace logs for last 7 days,
  returns formatted report string and deposits a FACTUAL node to cortex.

Also exports cloud_escape_rate_data() for programmatic access.
Forensic log: ~/.TheIgors/logs/tool_calls.log
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from devices.igor.tools.registry import Tool, registry

log = logging.getLogger(__name__)

_CATEGORY_ORDER = ["greeting", "status", "factual", "code", "design", "other", "_all"]
_CATEGORY_LABELS = {
    "greeting": "greeting",
    "status": "status",
    "factual": "factual",
    "code": "code/task",
    "design": "design/plan",
    "other": "other",
    "_all": "TOTAL",
}


def cloud_escape_rate_data(days: int = 7) -> dict:
    """Return raw cloud escape stats dict (category → {total, cloud, local, cloud_pct})."""
    from ..cognition.metrics import _cloud_escape_by_category

    return _cloud_escape_by_category(days=days)


def cloud_escape_rate_report(days: int = 7, deposit: bool = True, **_) -> str:
    """
    Generate cloud escape rate report for last `days` days, bucketed by intent category.
    If deposit=True, writes a FACTUAL memory node with the summary.
    Returns formatted report string.
    """
    try:
        data = cloud_escape_rate_data(days=days)
    except Exception as exc:
        log.error("cloud_escape_rate_report: failed to get data — %s", exc)
        return f"[cloud_escape_metric ERROR] data collection failed: {exc}"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"Cloud Escape Rate Report — last {days} days ({ts})",
        f"{'Category':<12}  {'Total':>6}  {'Cloud':>6}  {'Local':>6}  {'Escape%':>8}",
        "-" * 52,
    ]

    for cat in _CATEGORY_ORDER:
        if cat not in data:
            continue
        d = data[cat]
        label = _CATEGORY_LABELS.get(cat, cat)
        bar = "█" * int(d["cloud_pct"] / 10) + "░" * (10 - int(d["cloud_pct"] / 10))
        lines.append(
            f"{label:<12}  {d['total']:>6}  {d['cloud']:>6}  {d['local']:>6}  "
            f"{d['cloud_pct']:>6.1f}%  {bar}"
        )

    report = "\n".join(lines)
    log.info(
        "cloud_escape_rate_report|OK|days=%d|total=%d|cloud=%d|cloud_pct=%.1f",
        days,
        data.get("_all", {}).get("total", 0),
        data.get("_all", {}).get("cloud", 0),
        data.get("_all", {}).get("cloud_pct", 0.0),
    )

    if deposit:
        _deposit_factual(data, ts, days)

    return report


def _deposit_factual(data: dict, ts: str, days: int) -> None:
    """Deposit cloud escape summary as FACTUAL memory node so Igor can see his trajectory."""
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex(None)
        total_d = data.get("_all", {})
        code_d = data.get("code", {})
        factual_d = data.get("factual", {})

        narrative = (
            f"Cloud escape rate {ts} (last {days}d): "
            f"overall {total_d.get('cloud_pct', 0.0):.1f}% "
            f"({total_d.get('cloud', 0)}/{total_d.get('total', 0)} turns). "
            f"code={code_d.get('cloud_pct', 0.0):.1f}% "
            f"factual={factual_d.get('cloud_pct', 0.0):.1f}%."
        )
        node_id = f"CLOUD_ESCAPE_{ts.replace('-', '')}"
        metadata = {
            "category": "cloud_escape_metric",
            "date": ts,
            "days": days,
            "overall_pct": total_d.get("cloud_pct", 0.0),
            "by_category": {
                k: v.get("cloud_pct", 0.0) for k, v in data.items() if k != "_all"
            },
        }
        cortex.store(
            narrative=narrative,
            memory_type="FACTUAL",
            node_id=node_id,
            metadata=metadata,
        )
        log.info("cloud_escape_rate_report: deposited FACTUAL node %s", node_id)
    except Exception as exc:
        log.info("cloud_escape_rate_report: deposit failed (non-fatal) — %s", exc)


# ── Register ──────────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="cloud_escape_rate_report",
        description=(
            "T-cloud-escape-rate-metric: Weekly cloud escape rate report by intent category. "
            "Scans turn_trace logs for last 7 days. Buckets by greeting/status/factual/code/design/other. "
            "Reports cloud escape % per category. Deposits FACTUAL node for trajectory tracking. "
            "D316 primary progress metric."
        ),
        parameters={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to scan (default 7)",
                    "default": 7,
                },
                "deposit": {
                    "type": "boolean",
                    "description": "Whether to deposit a FACTUAL memory node (default true)",
                    "default": True,
                },
            },
            "required": [],
        },
        fn=cloud_escape_rate_report,
    )
)

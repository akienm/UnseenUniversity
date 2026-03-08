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
        header = f"errors.log — {len(entries)} total entries, showing latest {len(shown)}:\n"
        return header + "\n".join(shown)
    except Exception as e:
        return f"Error reading error log: {e}"


registry.register(Tool(
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
))


registry.register(Tool(
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
))

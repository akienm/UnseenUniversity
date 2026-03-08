"""
Metrics tool — Igor can call get_metrics_report to examine his own internals.
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

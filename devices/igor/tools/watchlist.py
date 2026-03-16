"""
Watchlist tool — list Igor's current watch habits (#240).

Watch habits fire a TWM salience boost when a watched concept is mentioned,
instead of dispatching an action. This tool lets Igor (and Akien) inspect
what is currently being watched for.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .registry import Tool, registry


def _get_cortex():
    db_path = os.getenv("IGOR_DB_PATH", "memory/igor.db")
    from ..memory.cortex import Cortex

    return Cortex(Path(db_path))


def list_watchlist() -> str:
    """Return Igor's current watch habits as a formatted list."""
    try:
        cortex = _get_cortex()
        habits = cortex.get_habits()
        watch_habits = [h for h in habits if h.metadata.get("habit_type") == "watch"]
        if not watch_habits:
            return "No watch habits currently active."
        now = datetime.now(timezone.utc)
        lines = ["Active watchlist:"]
        for h in watch_habits:
            label = h.metadata.get("watch_label", h.id)
            expires = h.metadata.get("watch_expires")
            w_type = h.metadata.get("watch_type", "general")
            employer = h.metadata.get("employer_id", "")
            status = ""
            if expires:
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if now > exp_dt:
                        status = " [expired]"
                    else:
                        days_left = (exp_dt - now).days
                        status = f" [expires in {days_left}d]"
                except (ValueError, TypeError):
                    status = f" [expires: {expires}]"
            emp_str = f" (for {employer})" if employer else ""
            lines.append(f"  • {label} [{w_type}]{emp_str}{status}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing watchlist: {e}"


registry.register(
    Tool(
        name="list_watchlist",
        description=(
            "List Igor's current watch habits — concepts, people, or resources "
            "Igor is actively monitoring in conversation."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=list_watchlist,
    )
)

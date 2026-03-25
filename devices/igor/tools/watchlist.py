"""
Watchlist tool — list Igor's current watch habits (#240) and error log (#271).

Watch habits fire a TWM salience boost when a watched concept is mentioned,
instead of dispatching an action. This tool lets Igor (and Akien) inspect
what is currently being watched for.

Error watchlist: When Igor hits unrecoverable external failures (Gmail auth,
SSH down, Ollama unreachable, budget exhausted), append to watchlist.md with
timestamp, error, attempted fix, and suggested action.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .registry import Tool, registry
from ..paths import paths


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


# ── Error Watchlist (unrecoverable failures) ────────────────────────────────────


def append_error_watchlist(
    error: str, attempted_fix: str = "", suggested_action: str = ""
) -> bool:
    """
    Log an unrecoverable external failure to watchlist.md.

    Args:
        error: Description of the failure (e.g., "Gmail auth failed: AuthError")
        attempted_fix: What was tried (e.g., "Refreshed OAuth token")
        suggested_action: What to do next (e.g., "Check Gmail app password")

    Returns:
        True if append succeeded, False otherwise.
    """
    try:
        watchlist_path = paths().instance / "watchlist.md"
        watchlist_path.parent.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = f"\n- **{ts}** — {error}"
        if attempted_fix:
            entry += f"\n  - Attempted: {attempted_fix}"
        if suggested_action:
            entry += f"\n  - Action: {suggested_action}"

        # Create file if it doesn't exist with header
        if not watchlist_path.exists():
            with open(watchlist_path, "w", encoding="utf-8") as f:
                f.write("# Igor Watchlist — Unrecoverable Failures\n")
                f.write(
                    "\nThese are errors Igor couldn't fix on his own. They require human intervention.\n"
                )

        # Append entry
        with open(watchlist_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")

        return True
    except Exception as _e:
        # Silently fail — we don't want watchlist append to crash Igor
        return False


def read_error_watchlist() -> list[dict]:
    """
    Read all entries from watchlist.md.

    Returns:
        List of dicts: {timestamp, error, attempted_fix, suggested_action}
        Empty list if file doesn't exist or on error.
    """
    try:
        watchlist_path = paths().instance / "watchlist.md"
        if not watchlist_path.exists():
            return []

        entries = []
        current = {}
        with open(watchlist_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if line.startswith("- **"):
                    # New entry: "- **YYYY-MM-DDTHH:MM:SSZ** — error message"
                    if current:
                        entries.append(current)
                    parts = line.split("** — ", 1)
                    ts = parts[0].replace("- **", "")
                    error = parts[1] if len(parts) > 1 else ""
                    current = {
                        "timestamp": ts,
                        "error": error,
                        "attempted_fix": "",
                        "suggested_action": "",
                    }
                elif line.strip().startswith("- Attempted:") and current:
                    current["attempted_fix"] = line.replace("- Attempted:", "").strip()
                elif line.strip().startswith("- Action:") and current:
                    current["suggested_action"] = line.replace("- Action:", "").strip()
        if current:
            entries.append(current)
        return entries
    except Exception as _e:
        return []


def count_error_watchlist() -> int:
    """Return count of entries in error watchlist."""
    return len(read_error_watchlist())

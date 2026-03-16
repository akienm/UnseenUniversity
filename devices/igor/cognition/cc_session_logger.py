"""
CC Session Logger — D094: cc-direct-habit-execution.

Logs every direct habit call (Claude Code → Igor via /api/execute_habit) to a
daily session log at ~/.TheIgors/logs/cc_session_YYYYMMDD.log.

Newest-first (prepend), consistent with all other forensic logs.
Format: timestamp|direction|execute_habit|habit_id|args=...|result=...|NNNms

Fire-and-forget: exceptions are swallowed so logging never crashes a caller.
"""

from datetime import datetime
from pathlib import Path

_LOG_DIR = Path.home() / ".TheIgors" / "logs"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — rotate to .old


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _prepend(log_name: str, entry: str) -> None:
    """Prepend one line to a log file, rotating at 10 MB."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOG_DIR / log_name
        if path.exists():
            if path.stat().st_size > _MAX_BYTES:
                old = path.with_suffix(".old")
                if old.exists():
                    old.unlink()
                path.rename(old)
                existing = ""
            else:
                existing = path.read_text(encoding="utf-8")
        else:
            existing = ""
        path.write_text(entry + "\n" + existing, encoding="utf-8")
    except Exception:
        pass


def log_habit_call(
    *,
    habit_id: str,
    args: dict,
    result: str,
    duration_ms: int,
    direction: str = "claude→igor",
) -> None:
    """
    Log one direct habit invocation to cc_session_YYYYMMDD.log.

    habit_id    — the memory ID of the habit called (e.g. "PROC_WHAT_TIME")
    args        — kwargs dict passed to tool.execute(); {} for no-arg habits
    result      — response text (truncated to 200 chars in the log entry)
    duration_ms — wall-clock milliseconds from call to return
    direction   — "claude→igor" (default) or "igor→claude" for future reverse calls
    """
    try:
        today = datetime.now().strftime("%Y%m%d")
        log_name = f"cc_session_{today}.log"

        args_str = str(args).replace("\n", " ")[:120]
        result_str = result.replace("\n", " ")[:200]

        entry = (
            f"{_ts()}|{direction}|execute_habit|{habit_id}"
            f"|args={args_str}"
            f"|result={result_str}"
            f"|{duration_ms}ms"
        )
        _prepend(log_name, entry)
    except Exception:
        pass

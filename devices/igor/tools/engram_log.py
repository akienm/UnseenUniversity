"""engram_log — logging primitive callable from inside code_ref/engram execution.

Usage inside a code_ref tool function:
    from igor.tools.engram_log import engram_log
    engram_log("habit woke up", level="info")

Callers (push_sources, main.py) must wrap dispatch with engram_execution_context
so engram_log knows the current habit_id without changing tool signatures.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from ..cognition.forensic_logger import _current_turn
from .registry import Tool, registry

log = logging.getLogger(__name__)

_ctx = threading.local()


@contextmanager
def engram_execution_context(
    habit_id: str, habit_name: str = ""
) -> Generator[None, None, None]:
    """Set thread-local context so engram_log knows which habit is running."""
    _ctx.habit_id = habit_id
    _ctx.habit_name = habit_name
    try:
        yield
    finally:
        _ctx.habit_id = None
        _ctx.habit_name = None


def engram_log(message: str, level: str = "info") -> None:
    """Log a message from inside a code_ref tool execution.

    Emits to Igor's logger and appends a structured entry to the current
    TurnContext under key "engram_logs" (list, appended not overwritten).
    """
    habit_id = getattr(_ctx, "habit_id", None) or "unknown"
    habit_name = getattr(_ctx, "habit_name", None) or ""

    getattr(log, level, log.info)("[engram:%s] %s", habit_id, message)

    turn_ctx = getattr(_current_turn, "ctx", None)
    if turn_ctx is not None:
        entry = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "habit_id": habit_id,
            "habit_name": habit_name,
            "level": level,
            "message": message,
        }
        turn_ctx.setdefault("engram_logs", []).append(entry)


def _engram_log_tool(message: str, level: str = "info") -> str:
    """Tool wrapper so code_ref habits can call engram_log via registry."""
    engram_log(message, level=level)
    return f"[engram_log] {level}: {message}"


registry.register(
    Tool(
        name="engram_log",
        description="Log a message from inside engram/code_ref execution.",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to log"},
                "level": {
                    "type": "string",
                    "enum": ["debug", "info", "warning", "error"],
                    "default": "info",
                },
            },
            "required": ["message"],
        },
        fn=_engram_log_tool,
    )
)

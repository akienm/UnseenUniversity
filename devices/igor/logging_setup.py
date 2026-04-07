"""
logging_setup.py — Configure the igor.* Python logging hierarchy (#343).

Call setup_logging() once at Igor boot, before any IgorBase subclasses are
instantiated. After this call every class that inherits IgorBase automatically
emits to the correct area log file and to the console — no per-module setup.

Handler tree:
  igor.*              → ConsoleHandler (INFO+), master.log (WARNING+)
  igor.cognition.*    → cognition.log (INFO+)
  igor.tools.*        → tools.log (INFO+)
  igor.network.*      → network.log (INFO+)
  igor.memory.*       → memory.log (INFO+)

Adding a new subscriber (Discord, web, etc.):
  handler = DiscordHandler(...)
  logging.getLogger("igor").addHandler(handler)
"""

from __future__ import annotations

import logging
import logging.handlers
import time
from pathlib import Path

_LOG_MAX_BYTES = 50 * 1024 * 1024  # 50 MB per file
_LOG_BACKUP_COUNT = 3  # keep .1 .2 .3


# ── TimerHandle ───────────────────────────────────────────────────────────────


class TimerHandle:
    """
    Lightweight structured timer for forensic logs.

    Usage:
        timer = log.get_timer("pe_chain.hypothesize", ticket="T-foo")
        # ... do work ...
        timer.stop(result="ok", tokens=412)
        # Emits one log line: name=pe_chain.hypothesize started=20260406... elapsed=3.142 ticket=T-foo result=ok tokens=412

    Created via logging_setup.get_timer(log, name, level=logging.DEBUG, **context).
    Do not instantiate directly.
    """

    __slots__ = ("_log", "_name", "_level", "_started", "_ctx", "_ts_str")

    def __init__(
        self,
        logger: logging.Logger,
        name: str,
        level: int,
        **context_kwargs,
    ) -> None:
        import datetime

        self._log = logger
        self._name = name
        self._level = level
        self._ctx = context_kwargs
        self._started = time.perf_counter()
        self._ts_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")

    def stop(self, **result_kwargs) -> float:
        """Emit a structured log line and return elapsed seconds."""
        elapsed = time.perf_counter() - self._started
        parts = [
            f"name={self._name}",
            f"started={self._ts_str}",
            f"elapsed={elapsed:.6f}",
        ]
        for k, v in {**self._ctx, **result_kwargs}.items():
            parts.append(f"{k}={v}")
        self._log.log(self._level, " ".join(parts))
        return elapsed


def get_timer(
    logger: logging.Logger,
    name: str,
    level: int = logging.DEBUG,
    **context_kwargs,
) -> TimerHandle:
    """
    Return a TimerHandle that starts immediately.

    logger:          the logging.Logger to emit to
    name:            timer name (e.g. "pe_chain.hypothesize")
    level:           log level for the stop() call (default DEBUG)
    context_kwargs:  key=value pairs included in every stop() line

    Example:
        timer = get_timer(log, "read_ticket", ticket=ticket_id)
        # ... work ...
        timer.stop(desc_len=len(desc))
    """
    return TimerHandle(logger, name, level, **context_kwargs)


# ── Console handler ───────────────────────────────────────────────────────────


class ConsoleHandler(logging.Handler):
    """
    Routes log records to the Rich console.

    Format matches the existing console.print() style in main.py:
      HHMMSS[module_leaf] message

    INFO → dim (subdued, same as pipeline trace lines)
    WARNING+ → bold yellow / red
    """

    _LEVEL_STYLE = {
        logging.DEBUG: "dim",
        logging.INFO: "dim",
        logging.WARNING: "bold yellow",
        logging.ERROR: "bold red",
        logging.CRITICAL: "bold red reverse",
    }

    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level)
        from rich.console import Console as _Console

        self._console = _Console()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts = time.strftime("%H%M%S")
            msg = self.format(record)
            # Use only the leaf module name for brevity: igor.cognition.ne → ne
            leaf = record.name.rsplit(".", 1)[-1] if "." in record.name else record.name
            style = self._LEVEL_STYLE.get(record.levelno, "dim")
            self._console.print(f"[{style}]{ts}[{leaf}] {msg}[/{style}]")
        except Exception:
            self.handleError(record)


# ── Setup ─────────────────────────────────────────────────────────────────────


def setup_logging(log_dir: Path) -> None:
    """
    Wire the igor.* logging hierarchy.
    Safe to call multiple times — handlers are only added once (idempotent).
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    igor_root = logging.getLogger("igor")

    # Idempotent guard — don't add duplicate handlers on restart/reimport
    if igor_root.handlers:
        return

    igor_root.setLevel(logging.DEBUG)
    igor_root.propagate = False  # Don't leak to Python root logger

    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    # Console — INFO+ from all igor.*
    ch = ConsoleHandler(level=logging.INFO)
    ch.setFormatter(fmt)
    igor_root.addHandler(ch)

    # Master log — WARNING+ from everything
    _add_file(igor_root, log_dir / "master.log", logging.WARNING, fmt)

    # Per-area handlers — INFO+ each subsystem; propagate=True so they also
    # flow up to igor root (console + master.log)
    for area, filename in [
        ("igor.cognition", "cognition.log"),
        ("igor.tools", "tools.log"),
        ("igor.network", "network.log"),
        ("igor.memory", "memory.log"),
    ]:
        area_logger = logging.getLogger(area)
        area_logger.propagate = True
        _add_file(area_logger, log_dir / filename, logging.INFO, fmt)

    # Per-module file handlers — auto-register for all igor/tools/*.py modules
    # so that logging.getLogger("igor.tools.<name>") goes to <name>.log
    _tools_dir = Path(__file__).parent / "tools"
    for _tool_file in sorted(_tools_dir.glob("*.py")):
        if _tool_file.stem.startswith("_"):
            continue
        _mod_logger = logging.getLogger(f"igor.tools.{_tool_file.stem}")
        _mod_logger.propagate = True
        _add_file(_mod_logger, log_dir / f"{_tool_file.stem}.log", logging.INFO, fmt)

    # tree_index in memory/ has its own forensic log
    _tree_logger = logging.getLogger("igor.memory.tree_index")
    _tree_logger.propagate = True
    _add_file(_tree_logger, log_dir / "tree_index.log", logging.INFO, fmt)


def _add_file(
    logger: logging.Logger, path: Path, level: int, fmt: logging.Formatter
) -> None:
    """Append a FileHandler if one for this path isn't already registered."""
    path_str = str(path)
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == path_str:
            return
    handler = logging.handlers.RotatingFileHandler(
        path_str,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(fmt)
    logger.addHandler(handler)

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
import time
from pathlib import Path

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


def _add_file(
    logger: logging.Logger, path: Path, level: int, fmt: logging.Formatter
) -> None:
    """Append a FileHandler if one for this path isn't already registered."""
    path_str = str(path)
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == path_str:
            return
    handler = logging.FileHandler(path_str, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(fmt)
    logger.addHandler(handler)

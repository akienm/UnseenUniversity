"""
igor_base.py — Base class for all long-lived Igor components.

Inherits DiagnosticBase (diagnostic_base.base) which provides:
  - self.logger   -> loguru TaggedLogger (native; use for new code)
  - self.info/debug/warning/error() -> loguru convenience methods
  - self.stopwatch() -> Stopwatch context manager
  - self.elapsed_s() -> seconds since construction
  - get_name()    -> hierarchical name (parent.own_name)
  - dump()        -> dict of public instance attributes

Adds for backward compat with existing IgorBase subclasses:
  - self.log      -> _IgorSafeLogger (stdlib logging → loguru via InterceptHandler)
  - log_llm_io()  -> LLM I/O flight recorder
  - log_state_snapshot() -> arbitrary state snapshot logger

stdlib → loguru intercept: installed once at module import so all
logging.getLogger() calls from existing Igor code flow through loguru
and hit the DiagnosticBase JSON file sink automatically.
# tags: Architecture, Cognition
"""

import json as _json
import logging
import sys
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger as _loguru_logger

from .paths import paths

# ── stdlib → loguru intercept ────────────────────────────────────────────────


class InterceptHandler(logging.Handler):
    """Routes all stdlib logging.Logger calls through loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = _loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        _loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


# Install once — all stdlib logging flows through loguru from this point on.
logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

# ── _EmergencySafeLogger + get_logger (backward compat re-exports) ───────────


class _EmergencySafeLogger:
    """stdlib logging wrapper with stderr fallback. Kept for backward compat.

    With InterceptHandler installed, calls route through loguru automatically.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._logger = logging.getLogger(name)

    def _emit(self, level: str, msg: str, *args) -> None:
        try:
            getattr(self._logger, level)(msg, *args)
        except Exception:
            try:
                formatted = msg % args if args else msg
            except Exception:
                formatted = repr(msg)
            print(
                f"[STDERR-FALLBACK][{level.upper()}][{self._name}] {formatted}",
                file=sys.stderr,
            )

    def debug(self, msg: str, *args) -> None:
        self._emit("debug", msg, *args)

    def info(self, msg: str, *args) -> None:
        self._emit("info", msg, *args)

    def warning(self, msg: str, *args) -> None:
        self._emit("warning", msg, *args)

    def error(self, msg: str, *args) -> None:
        self._emit("error", msg, *args)

    def exception(self, msg: str, *args) -> None:
        try:
            self._logger.exception(msg, *args)
        except Exception:
            try:
                formatted = msg % args if args else msg
            except Exception:
                formatted = repr(msg)
            import traceback

            tb = traceback.format_exc()
            print(
                f"[STDERR-FALLBACK][EXCEPTION][{self._name}] {formatted}\n{tb}",
                file=sys.stderr,
            )


def get_logger(name: str) -> _EmergencySafeLogger:
    """Return a logger for module-level code that cannot inherit IgorBase."""
    return _EmergencySafeLogger(name)


# ── _IgorSafeLogger — adds get_timer() ──────────────────────────────────────


class _IgorSafeLogger(_EmergencySafeLogger):
    """Extends _EmergencySafeLogger with Igor-specific get_timer()."""

    def get_timer(
        self,
        name: str,
        level: int = logging.DEBUG,
        **context_kwargs,
    ):
        """
        Return a TimerHandle that starts immediately.

        timer = self.log.get_timer("ne.process_turn", ticket="T-foo")
        # ... work ...
        timer.stop(result="ok", tokens=412)
        """
        from .logging_setup import get_timer as _get_timer

        return _get_timer(self._logger, name, level, **context_kwargs)


def _igor_get_logger(name: str) -> _IgorSafeLogger:
    return _IgorSafeLogger(name)


# ── IgorBase ─────────────────────────────────────────────────────────────────

# Ensure repo root is on sys.path for any remaining lab.* imports in subclasses
_repo_root = str(paths().source_root)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

_LOG_DIR = paths().logs

# Deferred import — diagnostic_base is in unseen_university, available via pip install -e
try:
    from diagnostic_base.base import DiagnosticBase as _DiagnosticBase

    _BASE = _DiagnosticBase
except ImportError:
    # Fallback: plain object so Igor still boots if ADC not installed
    _BASE = object  # type: ignore[assignment,misc]


class IgorBase(_BASE):  # type: ignore[valid-type,misc]
    """Igor-specific base — DiagnosticBase with paths().logs routing and self.log compat."""

    # JSON operational logs go to ~/.TheIgors/<device_id>/log/json/
    try:
        from pathlib import Path as _Path

        _log_root = paths().runtime
    except Exception:
        from pathlib import Path as _Path

        _log_root = _Path("datacenter_logs")

    _log_instance: Optional[_IgorSafeLogger] = None

    def __init__(self):
        try:
            super().__init__(device_id="igor")
        except TypeError:
            # Fallback when _BASE is object (no DiagnosticBase available)
            super().__init__()

    @property
    def log(self) -> _IgorSafeLogger:
        """Lazy per-class _IgorSafeLogger. Backward compat; routes through loguru.

        New code should use self.logger (native loguru TaggedLogger) or
        self.info/debug/warning/error() convenience methods.
        """
        if not isinstance(
            self.__class__.__dict__.get("_log_instance"), _IgorSafeLogger
        ):
            _module = type(self).__module__ or ""
            _name = (
                _module[len("devices.") :]
                if _module.startswith("devices.")
                else (_module or self.__class__.__name__)
            )
            self.__class__._log_instance = _igor_get_logger(_name)
        return self.__class__._log_instance

    def log_llm_io(
        self,
        step: str,
        prompt: str,
        response: str,
        model: str,
        elapsed_ms: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        """Log an LLM inference call to ~/.TheIgors/logs/llm_io/YYYYMMDD.log.

        Dual-write: file log (existing) + infra.llm_calls DB row
        (T-universal-llm-lineage — queryable without reading log files).
        """
        try:
            log_dir = paths().runtime / "logs" / "llm_io"
            log_dir.mkdir(parents=True, exist_ok=True)

            today = datetime.now().strftime("%Y%m%d")
            path = log_dir / f"{today}.log"

            entry = {
                "ts": datetime.now().isoformat(),
                "step": step,
                "model": model,
                "elapsed_ms": elapsed_ms,
                "prompt_len": len(prompt),
                "response_len": len(response),
                "prompt": prompt[:16384],
                "response": response[:8192],
            }

            with path.open("a", encoding="utf-8") as f:
                f.write(_json.dumps(entry) + "\n")
        except Exception as _e:
            self.log.error("log_llm_io failed: %s", _e)

        # T-universal-llm-lineage: DB write alongside file write (fire-and-forget)
        try:
            import hashlib
            import os as _os

            import psycopg2

            _db_url = _os.getenv("IGOR_HOME_DB_URL") or str(paths().home_db_url)
            if _db_url:
                _hash = hashlib.md5(
                    prompt.encode("utf-8", errors="replace")
                ).hexdigest()
                _inst = _os.getenv("IGOR_INSTANCE_ID", "")
                _conn = psycopg2.connect(_db_url)
                with _conn:
                    with _conn.cursor() as _cur:
                        _cur.execute(
                            "INSERT INTO infra.llm_calls "
                            "(prompt_hash, model, tokens_in, tokens_out, outcome, "
                            " source_fn, elapsed_ms, instance_id) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                            (
                                _hash,
                                model,
                                tokens_in,
                                tokens_out,
                                "pass",
                                step,
                                int(elapsed_ms),
                                _inst,
                            ),
                        )
                _conn.close()
        except Exception:
            pass

    def log_state_snapshot(self, label: str, state: Dict[str, Any]) -> None:
        """Log an arbitrary state snapshot to ~/.TheIgors/logs/snapshots/YYYYMMDD.log."""
        try:
            log_dir = paths().runtime / "logs" / "snapshots"
            log_dir.mkdir(parents=True, exist_ok=True)

            today = datetime.now().strftime("%Y%m%d")
            path = log_dir / f"{today}.log"

            entry = {
                "ts": datetime.now().isoformat(),
                "label": label,
                "state": state,
            }

            with path.open("a", encoding="utf-8") as f:
                f.write(_json.dumps(entry) + "\n")
        except Exception as _e:
            self.log.error("log_state_snapshot failed: %s", _e)

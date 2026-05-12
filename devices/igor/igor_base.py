"""
igor_base.py — Base class for all long-lived Igor components.

Thin subclass of AgentBase (lab/utility_closet/agent_base.py) that adds:
  - log_dir defaulting to ~/.TheIgors/logs/ (via paths())
  - get_timer() on the logger (Igor-specific logging_setup integration)
  - log_llm_io(step, prompt, response, model, elapsed_ms): flight-recorder LLM I/O logging
  - log_state_snapshot(label, state): arbitrary state snapshots for post-mortem analysis

All diagnostic capabilities (get_name, log, time_it, record_perf, dump,
_get_caller) come from AgentBase. Existing subclasses (Cortex, Thalamus,
NarrativeEngine, etc.) continue to work unchanged.

Pattern adapted from Akien's SWADLBase (swadl_base.py).
T-uc-base-class-extract
"""

import json as _json
import logging
import sys
from datetime import datetime
from typing import Any, Dict, Optional

from .paths import paths

# Ensure repo root is on sys.path for lab.utility_closet imports
_repo_root = str(paths().source_root)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Re-export AgentBase's logger and get_logger for backward compatibility.
# Many tool files do: from ..igor_base import get_logger
from lab.utility_closet.agent_base import (  # noqa: F401
    AgentBase,
    _EmergencySafeLogger,
    get_logger,
)

_LOG_DIR = paths().logs


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

        timer = self.log.get_timer("pe_chain.hypothesize", ticket="T-foo")
        # ... work ...
        timer.stop(result="ok", tokens=412)
        """
        from .logging_setup import get_timer as _get_timer

        return _get_timer(self._logger, name, level, **context_kwargs)


def _igor_get_logger(name: str) -> _IgorSafeLogger:
    """Return an Igor-specific logger with get_timer() support."""
    return _IgorSafeLogger(name)


class IgorBase(AgentBase):
    """Igor-specific base — AgentBase with paths().logs and get_timer()."""

    _logger: Optional[_IgorSafeLogger] = None

    def __init__(self):
        super().__init__(log_dir=_LOG_DIR)

    @property
    def log(self) -> _IgorSafeLogger:
        """
        Lazy per-class logger with emergency stderr fallback and get_timer().
        Logger name is the module path (igor.cognition.narrative_engine) so
        Python's logging hierarchy routes records to the correct area handler.
        """
        if not isinstance(self.__class__.__dict__.get("_logger"), _IgorSafeLogger):
            _module = type(self).__module__ or ""
            # Strip wild_igor. prefix so names sit under igor.* hierarchy
            _name = (
                _module[len("wild_igor.") :]
                if _module.startswith("wild_igor.")
                else (_module or self.__class__.__name__)
            )
            self.__class__._logger = _igor_get_logger(_name)
        return self.__class__._logger

    def log_llm_io(
        self,
        step: str,
        prompt: str,
        response: str,
        model: str,
        elapsed_ms: float,
    ) -> None:
        """
        Log an LLM inference call to ~/.TheIgors/logs/llm_io/YYYYMMDD.log.

        Fire-and-forget: catches all exceptions, never raises. Caps prompt at
        16KB, response at 8KB to prevent unbounded log growth.

        Args:
            step: phase name (e.g., 'pe_plan', 'pe_situate')
            prompt: the full prompt text sent to the LLM
            response: the full response received
            model: model identifier (e.g., 'claude-opus-4-6')
            elapsed_ms: inference latency in milliseconds
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

    def log_state_snapshot(self, label: str, state: Dict[str, Any]) -> None:
        """
        Log an arbitrary state snapshot to ~/.TheIgors/logs/snapshots/YYYYMMDD.log.

        Fire-and-forget: catches all exceptions, never raises. Useful for
        basket-at-escalation captures or other post-mortem forensics.

        Args:
            label: snapshot context (e.g., 'escalation', 'pe_implement_before')
            state: dict of state variables to snapshot
        """
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

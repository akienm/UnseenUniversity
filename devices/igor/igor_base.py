"""
igor_base.py — Base class for all long-lived Igor components.

Thin subclass of AgentBase (lab/utility_closet/agent_base.py) that adds:
  - log_dir defaulting to ~/.TheIgors/logs/ (via paths())
  - get_timer() on the logger (Igor-specific logging_setup integration)

All diagnostic capabilities (get_name, log, time_it, record_perf, dump,
_get_caller) come from AgentBase. Existing subclasses (Cortex, Thalamus,
NarrativeEngine, etc.) continue to work unchanged.

Pattern adapted from Akien's SWADLBase (swadl_base.py).
T-uc-base-class-extract
"""

import logging
import sys
from typing import Optional

from .paths import paths

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

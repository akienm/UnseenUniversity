"""stdlib → loguru intercept — the base-owned logging bridge.

Routes all stdlib ``logging`` records through loguru so they reach the
DiagnosticBase JSON file sink. This is base substrate: loguru ownership lives
in ``diagnostic_base`` (alongside ``TaggedLogger`` / ``DiagnosticBase``), not
inside any one device.

A consuming device installs the bridge explicitly at boot by calling
``install_stdlib_intercept()`` (Igor does so from ``igor_base``). The base does
**not** self-install: ``basicConfig(force=True)`` rewrites the root logger's
handlers process-wide, so that must stay a deliberate, opt-in act by the device
that wants it — not a side effect of importing the base (which tests, CLI tools,
and audit tooling all do).

Moved verbatim from ``devices/igor/igor_base.py`` (T-loguru-ownership-to-base):
behavior-preserving — same handler, same ``force=True`` install semantics. The
residue lived in Igor from the era when Igor was its own repo; the device
contract belongs in the shared base, and Igor is one consumer of it.
# tags: Architecture, Infrastructure
"""

from __future__ import annotations

import logging

from loguru import logger as _loguru_logger


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


def install_stdlib_intercept() -> None:
    """Install the stdlib→loguru intercept on the root logger.

    Replaces the root logger's handlers with a single ``InterceptHandler`` so
    every ``logging.getLogger(...)`` call flows through loguru. Converges on
    repeated calls (each call installs one handler), so it is safe to call once
    at each device's boot. Uses ``force=True`` to match the original install
    semantics this was extracted from.
    """
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

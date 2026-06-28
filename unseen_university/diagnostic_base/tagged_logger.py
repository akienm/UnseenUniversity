"""TaggedLogger — loguru proxy that adds tag-scoped sub-loggers on demand.

Usage:
    self.logger.info("plain log")
    self.logger.perf("stopwatch entry")       # INFO + tag=perf
    self.logger.perf.debug("verbose perf")    # DEBUG + tag=perf
"""

from __future__ import annotations

from loguru import logger as _root_logger


class TaggedLogger:
    """Thin loguru wrapper.

    At the top level (no tag) it delegates to loguru directly.
    Attribute access creates a tag-bound child: self.logger.perf → tag="perf".
    Calling a child directly is shorthand for INFO: self.logger.perf("msg").
    """

    def __init__(self, bound_logger=None, tag: str | None = None):
        self._logger = bound_logger if bound_logger is not None else _root_logger
        self._tag = tag

    # ── Standard levels ────────────────────────────────────────────────────
    def debug(self, msg: str, *args, **kwargs) -> None:
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self._logger.error(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        self._logger.exception(msg, *args, **kwargs)

    # ── Tag-scoped sub-logger ───────────────────────────────────────────────
    def __getattr__(self, tag: str) -> "_TagBoundLogger":
        # Avoid infinite recursion for private/dunder attributes
        if tag.startswith("_"):
            raise AttributeError(tag)
        return _TagBoundLogger(self._logger.bind(tag=tag), tag=tag)

    # ── Direct call on a tagged instance → INFO ────────────────────────────
    def __call__(self, msg: str, *args, **kwargs) -> None:
        self._logger.info(msg, *args, **kwargs)

    # ── Loguru pass-through for bind / opt / patch ─────────────────────────
    def bind(self, **kwargs) -> "TaggedLogger":
        return TaggedLogger(self._logger.bind(**kwargs), tag=self._tag)

    def opt(self, **kwargs) -> "TaggedLogger":
        return TaggedLogger(self._logger.opt(**kwargs), tag=self._tag)


class _TagBoundLogger(TaggedLogger):
    """A tag-bound sub-logger returned by TaggedLogger.__getattr__.

    self.logger.perf           → _TagBoundLogger(tag="perf")
    self.logger.perf("msg")    → INFO at tag=perf
    self.logger.perf.debug(m)  → DEBUG at tag=perf
    """

    def __init__(self, bound_logger, tag: str):
        super().__init__(bound_logger, tag=tag)

    def __getattr__(self, subtag: str) -> "_TagBoundLogger":
        if subtag.startswith("_"):
            raise AttributeError(subtag)
        combined = f"{self._tag}.{subtag}"
        return _TagBoundLogger(self._logger.bind(tag=combined), tag=combined)

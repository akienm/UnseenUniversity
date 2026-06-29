"""DiagnosticBase — merged SWADL + ADC base class.

From SWADL: instance naming via gc.get_referrers, hierarchical get_name,
            substitution engine, dump/bannerize, apply_kwargs, context manager,
            timeout_remaining, lazy per-class logger.
From ADC:   device_id stamping, logs path convention.
Logging:    loguru backend via TaggedLogger.
Operational logs: one JSON file per log record → <log_root>/<device_id>/log/json/.
  These are rolling operational logs (30-day retention), not durable knowledge.
  Call prune_json_logs() from day-close to enforce the retention window.
"""

from __future__ import annotations

import gc
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger as _root_logger

from .tagged_logger import TaggedLogger
from .perf import Stopwatch
from .logging_bridge import install_stdlib_intercept

# Per-(class, log_root) logger cache so subclasses see their own class name in log
# records AND a class logged under two different roots (e.g. across tests with a
# monkeypatched home) does not pin the first-resolved root for the rest of the run.
_logger_cache: dict[tuple, TaggedLogger] = {}

# Coarse per-device log streams (Akien 2026-06-25, T-per-device-log-hierarchy):
# every device's records route to ~/.unseen_university/logs/<device>/<stream>/,
# where <stream> is one of exactly three feed-aligned streams — info / warn / debug.
# The precise loguru level stays ON the record (filename suffix + payload "level"),
# so collapsing WARNING/ERROR/CRITICAL into "warn" loses no information — the stream
# is just the coarse routing the comm feeds + web buttons read (T-uu-readfeed,
# T-device-web-feed-channel-buttons).
_LEVEL_STREAMS = {
    "TRACE": "debug", "DEBUG": "debug",
    "INFO": "info", "SUCCESS": "info",
    "WARNING": "warn", "ERROR": "warn", "CRITICAL": "warn",
}


def _level_stream(level_name: str) -> str:
    """Map a loguru level name to its coarse feed stream (info/warn/debug)."""
    return _LEVEL_STREAMS.get(level_name.upper(), "info")


def _default_log_root() -> Path:
    """Canonical per-device log home — ``~/.unseen_university/logs`` (UU_HOME/logs).

    Resolution order (all at CALL TIME, so test redirection takes effect and the
    suite never writes into the real home):
      1. ``UU_LOG_ROOT`` env override — the hermetic-test knob, mirroring
         ``UU_MEMORY_ROOT`` / ``UU_DEVICES_ROOT``.
      2. ``uu_home()/logs`` — the canonical runtime location (lazy import keeps
         diagnostic_base layer-independent of unseen_university).
      3. ``~/.unseen_university/logs`` fallback if the import is mid-cycle on
         first boot (same canonical path).
    """
    env = os.environ.get("UU_LOG_ROOT")
    if env:
        return Path(env)
    try:
        from unseen_university._uu_root import uu_home

        return Path(uu_home()) / "logs"
    except Exception:
        return Path.home() / ".unseen_university" / "logs"

# Registered once on first DiagnosticBase instantiation
_json_sink_id: int | None = None

# stdlib → loguru bridge installed once on first DiagnosticBase instantiation.
# Owned by the base, triggered by ANY device boot — not by any one device
# (Igor is not special; it must not gate base logging for everyone else).
_intercept_installed: bool = False


def _json_file_sink(message) -> None:
    """Write one JSON file per log record. Silent noop on any error."""
    try:
        record = message.record
        extra = record["extra"]
        if "device_id" not in extra:
            return
        device_id = extra["device_id"]
        log_root = Path(extra["log_root"]) if extra.get("log_root") else _default_log_root()

        ts = record["time"]
        ts_str = ts.strftime("%Y%m%d-%H%M%S-") + f"{ts.microsecond:06d}"
        level = record["level"].name.lower()
        logger_name = (record["name"] or "unknown").replace(".", "_")[:40]

        # Canonical layout: <log_root>/<device>/<stream>/ — one file per record,
        # stream ∈ {info, warn, debug}. The exact level stays in the filename + payload.
        out_dir = log_root / device_id / _level_stream(record["level"].name)
        out_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "ts": ts.isoformat(),
            "level": record["level"].name,
            "logger": record["name"],
            "message": record["message"],
            "device_id": device_id,
        }
        if extra.get("class_name"):
            payload["class_name"] = extra["class_name"]
        if extra.get("tag"):
            payload["tag"] = extra["tag"]

        filename = f"{ts_str}_{logger_name}_{level}.json"
        (out_dir / filename).write_text(
            json.dumps(payload, default=str), encoding="utf-8"
        )
    except Exception:
        pass


def prune_json_logs(log_root: Path | str | None = None, days: int = 30) -> int:
    """Delete JSON log files and trace JSONL records older than `days` days.

    Call from day-close to enforce the 30-day rolling retention window.
    Each device prunes its own local log tree (log/json/ and trace/).
    Returns count of files deleted.
    """
    import time as _time

    root = Path(log_root) if log_root else _default_log_root()
    if not root.exists():
        return 0
    cutoff = _time.time() - days * 86400
    deleted = 0
    # Canonical per-level streams + legacy log/json layout + dispatch traces.
    for pattern in (
        "info/*.json", "warn/*.json", "debug/*.json",
        "log/json/*.json",  # legacy layout — prune leftover files from before the split
        "trace/*.jsonl",
    ):
        for f in root.rglob(pattern):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
            except Exception:
                pass
    return deleted


class _SafeDict(dict):
    """dict subclass that leaves unknown keys un-substituted rather than raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class DiagnosticBase:
    """Logging + perf + instance-naming base.

    Subclass and call super().__init__(**kwargs) from your __init__.

        class MyDevice(DiagnosticBase):
            def __init__(self, **kwargs):
                super().__init__(device_id="my_device", **kwargs)
    """

    def __init__(
        self,
        *,
        name: str = "",
        device_id: str = "",
        parent: "DiagnosticBase | None" = None,
        **kwargs: Any,
    ):
        global _json_sink_id, _intercept_installed
        # Per-instance log-root override (set via the _log_root setter or kwargs);
        # None means "use the canonical default", resolved at call time.
        self.__log_root_override: Path | None = None
        self._device_id = device_id or type(self).__name__.lower()
        self._parent = parent
        # Explicit name wins; gc lookup is a best-effort fallback for module/class-scope vars
        self._instance_name: str | None = name or None
        self._start_time = time.monotonic()
        self.apply_kwargs(**kwargs)
        # Register the JSON file sink once — all future log calls write one file per record
        if _json_sink_id is None:
            _json_sink_id = _root_logger.add(
                _json_file_sink, enqueue=False, backtrace=False, diagnose=False
            )
        # Install the stdlib→loguru intercept once, on first device boot, so
        # stdlib logging.getLogger() calls from any device flow into loguru and
        # this JSON sink. Base-owned + boot-triggered (not import-triggered, so
        # tests/CLI tooling that merely import the base aren't affected).
        if not _intercept_installed:
            install_stdlib_intercept()
            _intercept_installed = True

    # ── Instance naming (SWADL gc trick) ─────────────────────────────────────

    def _get_instance_names(self) -> list[str]:
        """Return variable names this instance is bound to in dict-backed scopes.

        CPython fast-locals (function frames) are NOT heap dicts — gc.get_referrers
        won't find them. This reliably finds names in module, class, and instance
        __dict__ scopes. Pass name= explicitly for function-local variables.
        """
        names: list[str] = []
        for ref in gc.get_referrers(self):
            if isinstance(ref, dict):
                for k, v in ref.items():
                    if v is self and isinstance(k, str) and not k.startswith("_"):
                        names.append(k)
        return names

    @property
    def _own_name(self) -> str:
        if self._instance_name is None:
            names = self._get_instance_names()
            self._instance_name = names[0] if names else type(self).__name__.lower()
        return self._instance_name

    def get_name(self) -> str:
        """Return hierarchical name: parent.name.own_name (no test prefix here)."""
        if self._parent is not None:
            return f"{self._parent.get_name()}.{self._own_name}"
        return self._own_name

    def __str__(self) -> str:
        return f"<{type(self).__name__} name={self.get_name()}>"

    # ── Logging ──────────────────────────────────────────────────────────────

    @property
    def _log_root(self) -> Path:
        """Where this instance's log files land.

        Defaults to the canonical per-device home (``~/.unseen_university/logs``),
        resolved at CALL TIME so test monkeypatching of uu_home takes effect and the
        suite never writes into the real home. A subclass may pin a different root by
        assigning a class attribute ``_log_root = <path>`` (which shadows this
        property), and a single instance may pin one via ``obj._log_root = <path>``
        (handled by the setter below).
        """
        if self.__log_root_override is not None:
            return self.__log_root_override
        return _default_log_root()

    @_log_root.setter
    def _log_root(self, value: Path | str | None) -> None:
        self.__log_root_override = None if value is None else Path(value)

    @property
    def logger(self) -> TaggedLogger:
        """Lazy per-(class, root) TaggedLogger bound with class, device, and log_root."""
        cls = type(self)
        root_str = str(self._log_root)
        key = (cls, root_str)
        cached = _logger_cache.get(key)
        if cached is None:
            bound = _root_logger.bind(
                class_name=cls.__name__,
                device_id=self._device_id,
                log_root=root_str,
            )
            cached = TaggedLogger(bound)
            _logger_cache[key] = cached
        return cached

    def debug(self, msg: str, *args, **kwargs) -> None:
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self.logger.error(msg, *args, **kwargs)

    # ── Performance stopwatch factory ─────────────────────────────────────────

    def stopwatch(self, stopwatch_id: str, *, comment: str = "") -> Stopwatch:
        """Return a Stopwatch pre-bound to this instance's device_id and class."""
        return Stopwatch(
            stopwatch_id,
            device_id=self._device_id,
            class_name=type(self).__name__,
            comment=comment,
            log_root=self._log_root,
        )

    # ── Substitution engine (SWADL _SafeDict) ────────────────────────────────

    @staticmethod
    def resolve_substitutions(template: str, context: dict, max_iter: int = 20) -> str:
        """Repeatedly expand {key} substitutions until stable or max_iter reached."""
        safe = _SafeDict(context)
        result = template
        for _ in range(max_iter):
            expanded = result.format_map(safe)
            if expanded == result:
                break
            result = expanded
        return result

    # ── kwargs absorption ────────────────────────────────────────────────────

    def apply_kwargs(self, **kwargs: Any) -> None:
        """Set any keyword arguments as instance attributes.

        Lets subclasses accept arbitrary config without enumerating every param.
        Unknown keys become attributes; no exception is raised.
        """
        for k, v in kwargs.items():
            setattr(self, k, v)

    # ── Dump / bannerize ─────────────────────────────────────────────────────

    def dump(self) -> dict:
        """Return a dict of public instance attributes (useful for logging state)."""
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}

    def bannerize(self, width: int = 60) -> str:
        """Return a readable banner of this instance's public state."""
        lines = [f"{'─' * width}", f"  {self}", f"{'─' * width}"]
        for k, v in self.dump().items():
            lines.append(f"  {k}: {v!r}")
        lines.append(f"{'─' * width}")
        return "\n".join(lines)

    # ── Timestamps ───────────────────────────────────────────────────────────

    @staticmethod
    def get_timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def elapsed_s(self) -> float:
        """Seconds since this instance was constructed."""
        return time.monotonic() - self._start_time

    def timeout_remaining(self, timeout_s: float) -> float:
        """Seconds remaining before timeout_s is exhausted. Negative if expired."""
        return timeout_s - self.elapsed_s()

    # ── Structured trace ─────────────────────────────────────────────────────
    #
    # trace_record() writes one JSON line per event to trace/<YYYYMMDD>.jsonl.
    # When debug_mode is True the same event is also emitted via the loguru
    # logger at DEBUG level so it appears in the live console.
    #
    # last_traces() reads from the on-disk JSONL files — most-recent first.

    debug_mode: bool = False

    def trace_record(self, event: str, data: "dict | None" = None) -> None:
        """Append one structured trace event to trace/<YYYYMMDD>.jsonl.

        Each record: {ts, device, event, data}. Silent noop on any I/O error.
        When debug_mode is True, also logs via self.logger.debug().
        """
        try:
            ts = datetime.now(timezone.utc)
            record: dict = {
                "ts": ts.isoformat(),
                "device": self._device_id,
                "event": event,
            }
            if data is not None:
                record["data"] = data

            trace_dir = self._log_root / self._device_id / "trace"
            trace_dir.mkdir(parents=True, exist_ok=True)
            day_file = trace_dir / f"{ts.strftime('%Y%m%d')}.jsonl"
            with day_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")

            if self.debug_mode:
                self.logger.debug(f"trace: {event} {data or ''}")
        except Exception:
            pass

    def last_traces(
        self,
        n: int = 20,
        since: "datetime | None" = None,
    ) -> "list[dict]":
        """Return the N most-recent trace records, newest first.

        Reads all JSONL files under trace/ for this device and returns records
        sorted descending by ts. If `since` is supplied, only records with
        ts >= since are returned (before the n-cap).
        """
        trace_dir = self._log_root / self._device_id / "trace"
        if not trace_dir.exists():
            return []
        records: list[dict] = []
        for jsonl in sorted(trace_dir.glob("*.jsonl"), reverse=True):
            try:
                for raw in jsonl.read_text(encoding="utf-8").splitlines():
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except Exception:
                        continue
                    if since is not None:
                        try:
                            from datetime import timezone as _tz

                            rec_ts = datetime.fromisoformat(rec["ts"])
                            if rec_ts.tzinfo is None:
                                rec_ts = rec_ts.replace(tzinfo=_tz.utc)
                            cmp_since = since
                            if cmp_since.tzinfo is None:
                                cmp_since = cmp_since.replace(tzinfo=_tz.utc)
                            if rec_ts < cmp_since:
                                continue
                        except Exception:
                            pass
                    records.append(rec)
            except Exception:
                pass
        records.sort(key=lambda r: r.get("ts", ""), reverse=True)
        return records[:n]

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "DiagnosticBase":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False

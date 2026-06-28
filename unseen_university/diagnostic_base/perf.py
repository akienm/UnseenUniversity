"""Stopwatch — context manager + explicit form for performance-point logging.

Logs to loguru (tag=perf) AND appends a row to a rolling per-day CSV at:
    <log_root>/<device_id>/perf/YYYY-MM-DD.perf.csv

CSV columns: ts_start, ts_end, device_id, class_name, stopwatch_id,
             comment, elapsed_s, success
"""

from __future__ import annotations

import csv
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from loguru import logger as _root_logger

_PERF_LOGGER = _root_logger.bind(tag="perf")
_PRUNE_DAYS = 10
_CSV_COLUMNS = [
    "ts_start",
    "ts_end",
    "device_id",
    "class_name",
    "stopwatch_id",
    "comment",
    "elapsed_s",
    "success",
]


def _csv_path(log_root: Path, device_id: str) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p = log_root / device_id / "perf"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{today}.perf.csv"


def _prune_old_csvs(perf_dir: Path, keep_days: int = _PRUNE_DAYS) -> None:
    cutoff = time.time() - keep_days * 86400
    for f in perf_dir.glob("*.perf.csv"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass


def _append_row(
    log_root: Path,
    device_id: str,
    class_name: str,
    stopwatch_id: str,
    comment: str,
    ts_start: float,
    ts_end: float,
    success: bool,
) -> None:
    csv_file = _csv_path(log_root, device_id)
    _prune_old_csvs(csv_file.parent)
    write_header = not csv_file.exists() or csv_file.stat().st_size == 0
    with csv_file.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "ts_start": datetime.fromtimestamp(
                    ts_start, tz=timezone.utc
                ).isoformat(),
                "ts_end": datetime.fromtimestamp(ts_end, tz=timezone.utc).isoformat(),
                "device_id": device_id,
                "class_name": class_name,
                "stopwatch_id": stopwatch_id,
                "comment": comment,
                "elapsed_s": round(ts_end - ts_start, 6),
                "success": success,
            }
        )


class Stopwatch:
    """Measures elapsed time and persists perf rows.

    Context-manager form (recommended):
        with self.stopwatch("load_page") as t:
            do_something()
        # t.elapsed_s available after the block

    Explicit form:
        t = Stopwatch("query_db", device_id="librarian", log_root=Path("..."))
        t.start()
        try:
            do_something()
            t.stop(success=True)
        except Exception:
            t.stop(success=False)
            raise
    """

    def __init__(
        self,
        stopwatch_id: str,
        *,
        device_id: str = "unknown",
        class_name: str = "",
        comment: str = "",
        log_root: Path | None = None,
    ):
        self.stopwatch_id = stopwatch_id
        self.device_id = device_id
        self.class_name = class_name
        self.comment = comment
        self.log_root = log_root or Path("datacenter_logs")
        self._ts_start: float | None = None
        self._ts_end: float | None = None
        self.elapsed_s: float | None = None
        self.success: bool | None = None

    def start(self) -> "Stopwatch":
        self._ts_start = time.monotonic()
        return self

    def stop(self, success: bool = True) -> "Stopwatch":
        if self._ts_start is None:
            raise RuntimeError("Stopwatch.stop() called before start()")
        self._ts_end = time.monotonic()
        self.elapsed_s = self._ts_end - self._ts_start
        self.success = success
        wall_start = time.time() - self.elapsed_s
        wall_end = time.time()
        _PERF_LOGGER.info(
            "{id} elapsed={elapsed:.4f}s success={ok}",
            id=self.stopwatch_id,
            elapsed=self.elapsed_s,
            ok=success,
        )
        try:
            _append_row(
                self.log_root,
                self.device_id,
                self.class_name,
                self.stopwatch_id,
                self.comment,
                wall_start,
                wall_end,
                success,
            )
        except Exception as exc:
            _PERF_LOGGER.warning(
                "CSV write failed for {id}: {exc}", id=self.stopwatch_id, exc=exc
            )
        return self

    def __enter__(self) -> "Stopwatch":
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.stop(success=exc_type is None)
        return False  # never suppress exceptions


@contextmanager
def timed(
    stopwatch_id: str,
    *,
    device_id: str = "unknown",
    class_name: str = "",
    comment: str = "",
    log_root: Path | None = None,
) -> Generator[Stopwatch, None, None]:
    """Functional context manager alias — useful outside class methods."""
    sw = Stopwatch(
        stopwatch_id,
        device_id=device_id,
        class_name=class_name,
        comment=comment,
        log_root=log_root,
    )
    with sw:
        yield sw

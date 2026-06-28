"""
Misfire counter — track repeated tool dispatch failures and surface repair candidates.

A misfire is:
  1. run_bash exit code 127 (command not found) where command matches a registered tool name
  2. Any tool call that errors with the same error type N times in a rolling window

Storage: misfire_log.jsonl in ~/.unseen_university/logs/
  Each entry: {timestamp, counter_key, error_type, count, threshold_exceeded}

Surfacing: when count > threshold, log_error + flag for habit repair review (audit Step 12).
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, NamedTuple
from ..igor_base import IgorBase

logger = logging.getLogger(__name__)


class MisfireRecord(NamedTuple):
    """A single misfire counter record."""

    timestamp: float  # unix timestamp
    counter_key: str  # (attempted_name, dispatch_path, error_type)
    error_type: str
    count: int
    threshold_exceeded: bool


class MisfireCounter(IgorBase):
    """
    Track repeated tool dispatch failures.

    Counter key: (attempted_name, dispatch_path, error_type)
    Threshold: configurable, default=3
    Window: rolling 24h
    """

    DEFAULT_THRESHOLD = 3
    DEFAULT_WINDOW_HOURS = 24

    def __init__(
        self,
        log_path: Optional[Path] = None,
        threshold: int = DEFAULT_THRESHOLD,
        window_hours: int = DEFAULT_WINDOW_HOURS,
    ):
        """
        Args:
            log_path: Path to misfire_log.jsonl. Defaults to ~/.unseen_university/logs/misfire_log.jsonl
            threshold: Counter threshold (default 3)
            window_hours: Rolling window duration in hours (default 24)
        """
        if log_path is None:
            from ..paths import paths as _paths

            log_path = _paths().logs / "misfire_log.jsonl"

        self.log_path = Path(log_path)
        self.threshold = threshold
        self.window_hours = window_hours

        # Ensure log directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record_bash_exit(
        self, command: str, exit_code: int, dispatch_path: str = "bash"
    ) -> bool:
        """
        Record a bash exit code. Return True if threshold exceeded.

        Only records exit code 127 (command not found).
        If the command matches a registered tool name, tracks it.
        """
        if exit_code != 127:
            return False

        # Extract attempted command name (first word)
        attempted_name = command.split()[0] if command else "unknown"
        error_type = "bash_exit_127"

        return self._increment_counter(attempted_name, dispatch_path, error_type)

    def record_tool_error(
        self, tool_name: str, error_type: str, dispatch_path: str = "tool_execute"
    ) -> bool:
        """
        Record a tool execution error. Return True if threshold exceeded.

        Args:
            tool_name: Name of the tool that failed
            error_type: Type of error (e.g., "ValueError", "TimeoutError", "RuntimeError")
            dispatch_path: Where the tool was called from (default "tool_execute")
        """
        return self._increment_counter(tool_name, dispatch_path, error_type)

    def _increment_counter(
        self, attempted_name: str, dispatch_path: str, error_type: str
    ) -> bool:
        """
        Increment counter for (attempted_name, dispatch_path, error_type).
        Return True if threshold exceeded.
        """
        counter_key = f"{attempted_name}|{dispatch_path}|{error_type}"

        # Read existing records
        records = self._read_log()

        # Filter to current window
        now = datetime.now()
        cutoff = now - timedelta(hours=self.window_hours)
        cutoff_timestamp = cutoff.timestamp()

        within_window = [r for r in records if r.timestamp >= cutoff_timestamp]

        # Count existing entries for this key
        count = sum(1 for r in within_window if r.counter_key == counter_key)
        count += 1  # Add this new one

        threshold_exceeded = count > self.threshold

        # Write new record
        record = {
            "timestamp": now.timestamp(),
            "counter_key": counter_key,
            "attempted_name": attempted_name,
            "dispatch_path": dispatch_path,
            "error_type": error_type,
            "count": count,
            "threshold_exceeded": threshold_exceeded,
            "iso_timestamp": now.isoformat(),
        }

        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"Failed to write misfire log: {e}")
            return False

        if threshold_exceeded:
            logger.error(
                f"MISFIRE THRESHOLD EXCEEDED: {counter_key} "
                f"(count={count}, threshold={self.threshold})"
            )

        return threshold_exceeded

    def _read_log(self) -> list[MisfireRecord]:
        """Read all records from the misfire log."""
        if not self.log_path.exists():
            return []

        records = []
        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        record = MisfireRecord(
                            timestamp=data["timestamp"],
                            counter_key=data["counter_key"],
                            error_type=data["error_type"],
                            count=data["count"],
                            threshold_exceeded=data["threshold_exceeded"],
                        )
                        records.append(record)
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(
                            f"Malformed misfire log entry: {line.strip()[:80]} — {e}"
                        )
        except Exception as e:
            logger.error(f"Failed to read misfire log: {e}")

        return records

    def get_threshold_exceeded(self) -> list[MisfireRecord]:
        """Return all records where threshold was exceeded."""
        records = self._read_log()
        return [r for r in records if r.threshold_exceeded]

    def get_active_counters(self) -> dict[str, int]:
        """
        Return current counter values (within rolling window) for all keys.
        Format: {counter_key: count}
        """
        records = self._read_log()

        now = datetime.now()
        cutoff = now - timedelta(hours=self.window_hours)
        cutoff_timestamp = cutoff.timestamp()

        within_window = [r for r in records if r.timestamp >= cutoff_timestamp]

        counters = {}
        for record in within_window:
            # Count is already computed in the record, but we recompute to be safe
            key = record.counter_key
            counters[key] = counters.get(key, 0) + 1

        return counters

    def reset_counter(self, counter_key: str) -> None:
        """
        Reset a specific counter by clearing it from the log.
        (In a real system, might mark as reviewed in DB instead.)
        """
        records = self._read_log()
        filtered = [r for r in records if r.counter_key != counter_key]

        try:
            with open(self.log_path, "w") as f:
                for record in filtered:
                    data = {
                        "timestamp": record.timestamp,
                        "counter_key": record.counter_key,
                        "error_type": record.error_type,
                        "count": record.count,
                        "threshold_exceeded": record.threshold_exceeded,
                    }
                    f.write(json.dumps(data) + "\n")
        except Exception as e:
            logger.error(f"Failed to reset counter: {e}")


# Global instance
_misfire_counter = None


def get_misfire_counter(
    threshold: int = MisfireCounter.DEFAULT_THRESHOLD,
) -> MisfireCounter:
    """Get or create the global misfire counter instance."""
    global _misfire_counter
    if _misfire_counter is None:
        _misfire_counter = MisfireCounter(threshold=threshold)
    return _misfire_counter

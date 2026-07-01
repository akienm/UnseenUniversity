"""
resource_monitor.py — live re-measurement of source speed (T-router-live-resource-read).

Increment 4 of D-inference-cost-optimizing-router. The headline principle of the whole
router: a source's `time_bucket` must reflect its CURRENT speed, not a baked-in label.
A box that got faster (Hex collapsed a bucket — an 8B job that needed a >5-min timeout on
old hardware now runs in seconds) must be promoted to `interactive`, or a stale
"ollama = slow" label exiles it from work it can now do. Difficulty has a corrector in the
selector (mechanical-failure escalation); a wrong `time_bucket` had NONE — this is that
feedback path.

Design:
  - Observed dispatch latencies feed a rolling window per source.
  - Once the window has enough samples, the MEDIAN maps to a time_bucket (median gives
    hysteresis for free — one outlier can't move it).
  - When the derived bucket differs from the source's current one, it is written back
    (Source.time_bucket is mutable for exactly this reason) and the TRANSITION is logged
    as a state change. The selector reads the new bucket on the very next call.

Latency-driven demotion also sheds load: an overloaded-but-up source answers slower, its
median rises, and it is demoted out of interactive eligibility — no separate load-shedder.
"""

from __future__ import annotations

import logging
import statistics
from collections import deque

from unseen_university.devices.inference.routing_buckets import TIME_BUCKETS

log = logging.getLogger(__name__)

# Bucket thresholds (seconds). interactive = a person waits on it; minutes = tolerable for
# non-interactive work; overnight = batch/node-absorption only. The ~5-min boundary matches
# the old akiensyoga9i 8B timeout that defined the "overnight" class.
INTERACTIVE_MAX_S = 10.0
MINUTES_MAX_S = 300.0

_DEFAULT_WINDOW = 5
_DEFAULT_MIN_SAMPLES = 3


def bucket_for_latency(seconds: float) -> str:
    """Map an observed latency (seconds) to a TIME_BUCKETS bucket."""
    if seconds <= INTERACTIVE_MAX_S:
        return "interactive"
    if seconds <= MINUTES_MAX_S:
        return "minutes"
    return "overnight"


class ResourceMonitor:
    """Keeps a rolling latency window per source and re-derives its time_bucket live.

    window: how many recent latencies to keep per source.
    min_samples: how many samples the window needs before it will move a bucket — one
        cold reading must not flip a source's classification.
    """

    def __init__(self, window: int = _DEFAULT_WINDOW, min_samples: int = _DEFAULT_MIN_SAMPLES) -> None:
        self._window = window
        self._min_samples = min_samples
        self._latencies: dict[str, deque[float]] = {}

    def record(self, source, latency_s: float) -> None:
        """Record one observed dispatch latency for `source` and re-derive its time_bucket.

        `source` is any object with a `.name` and a mutable `.time_bucket` (a Source). When
        the rolling-window median crosses a threshold, the bucket is written back and the
        transition logged. Below min_samples, nothing moves (the window is still filling).
        """
        name = getattr(source, "name", "?")
        buf = self._latencies.setdefault(name, deque(maxlen=self._window))
        buf.append(float(latency_s))
        if len(buf) < self._min_samples:
            return
        median = statistics.median(buf)
        new_bucket = bucket_for_latency(median)
        current = getattr(source, "time_bucket", None)
        if new_bucket != current and new_bucket in TIME_BUCKETS:
            log.info(
                "resource_monitor: %s time_bucket %s -> %s (median=%.1fs over %d samples)",
                name, current, new_bucket, median, len(buf),
            )
            source.time_bucket = new_bucket

    def window(self, source_name: str) -> list[float]:
        """Return the current latency window for a source (for inspection/tests)."""
        return list(self._latencies.get(source_name, ()))

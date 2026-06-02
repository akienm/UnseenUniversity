"""
pattern_tracker.py — PA2.0 Layer 1→2: observe dispatch outcomes, discover routing patterns.

Appends every non-trivial outcome to a flat JSONL corpus and aggregates
rates by tag, task_class (tier), and size. Exposes pattern_summary() for
context-load and posts PATTERN_REPORT to the shared channel every N dispatches.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_CORPUS = (
    Path(os.environ.get("GRANNY_HOME", str(Path.home() / ".granny"))) / "patterns.jsonl"
)
_REPORT_EVERY = int(os.environ.get("GRANNY_PATTERN_REPORT_INTERVAL", "50"))


class PatternTracker:
    """Thread-safe accumulator for dispatch outcome signals.

    Each record written to corpus:
      {ts, ticket_id, tags, task_class, size, signal, iterations, cost_usd}
    """

    def __init__(
        self, corpus_path: Path | None = None, report_every: int = _REPORT_EVERY
    ) -> None:
        self._path = corpus_path or _DEFAULT_CORPUS
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._report_every = report_every
        self._lock = threading.Lock()
        self._dispatch_count = 0
        # In-memory aggregates: (tag, task_class, size) → {signal: count}
        self._counts: dict[tuple, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

    def record(
        self,
        ticket_id: str,
        tags: list[str],
        task_class: str,
        size: str,
        signal: str,
        iterations: int = 0,
        cost_usd: float = 0.0,
        advisor_signal: str | None = None,
        wall_minutes: float | None = None,
    ) -> None:
        """Append one outcome to the corpus and update in-memory aggregates."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ticket_id": ticket_id,
            "tags": tags,
            "task_class": task_class,
            "size": size,
            "signal": signal,
            "iterations": iterations,
            "cost_usd": cost_usd,
        }
        if advisor_signal:
            entry["advisor_signal"] = advisor_signal
        if wall_minutes is not None:
            entry["wall_minutes"] = round(wall_minutes, 2)

        with self._lock:
            try:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError as e:
                log.warning("PatternTracker: failed to write corpus: %s", e)

            for tag in tags:
                self._counts[(tag, task_class, size)][signal] += 1
            self._dispatch_count += 1

        log.info(
            "PatternTracker: recorded %s ticket=%s tag=%s tier=%s size=%s iters=%d cost=%.4f",
            signal,
            ticket_id,
            tags,
            task_class,
            size,
            iterations,
            cost_usd,
        )

    def pattern_summary(self) -> dict:
        """Return aggregated rates by (tag, task_class, size).

        Shape: {
            "total_dispatches": int,
            "patterns": [
                {
                    "tag": str, "task_class": str, "size": str,
                    "total": int, "done_pct": float, "escalate_pct": float,
                    "signals": {signal: count}
                },
                ...
            ]
        }
        """
        with self._lock:
            patterns = []
            for (tag, task_class, size), signals in self._counts.items():
                total = sum(signals.values())
                done = signals.get("DONE", 0)
                escalate = sum(v for k, v in signals.items() if "ESCALATE" in k)
                patterns.append(
                    {
                        "tag": tag,
                        "task_class": task_class,
                        "size": size,
                        "total": total,
                        "done_pct": round(done / total * 100, 1) if total else 0.0,
                        "escalate_pct": (
                            round(escalate / total * 100, 1) if total else 0.0
                        ),
                        "signals": dict(signals),
                    }
                )
            patterns.sort(key=lambda p: p["escalate_pct"], reverse=True)
            return {"total_dispatches": self._dispatch_count, "patterns": patterns}

    def p90_minutes(self, size: str, min_samples: int = 10) -> float | None:
        """Return 90th-percentile wall_minutes for DONE tickets of this size class.

        Returns None when fewer than min_samples DONE outcomes with wall_minutes
        are available — caller should fall back to the fixed default timeout.
        """
        samples: list[float] = []
        try:
            if not self._path.exists():
                return None
            with self._path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        rec.get("size") == size
                        and rec.get("signal") == "DONE"
                        and rec.get("wall_minutes") is not None
                    ):
                        samples.append(float(rec["wall_minutes"]))
        except OSError:
            return None

        if len(samples) < min_samples:
            return None

        samples.sort()
        idx = int(len(samples) * 0.9)
        return samples[min(idx, len(samples) - 1)]

    def should_report(self) -> bool:
        with self._lock:
            return (
                self._report_every > 0
                and self._dispatch_count % self._report_every == 0
            )

    def format_report(self) -> str:
        """Format a PATTERN_REPORT channel post (top 5 patterns by escalation rate)."""
        summary = self.pattern_summary()
        top = summary["patterns"][:5]
        lines = [f"PATTERN_REPORT|total={summary['total_dispatches']}"]
        for p in top:
            lines.append(
                f"  {p['tag']}/{p['size']}/{p['task_class']}: "
                f"done={p['done_pct']}% escalate={p['escalate_pct']}% "
                f"n={p['total']}"
            )
        return "\n".join(lines)

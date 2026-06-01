"""
escalation_corpus.py — append-only non-DONE dispatch outcome log.

Every non-DONE WorkerResult from inference_dispatch_fn is appended here
so the routing compiler has a rich signal corpus to analyze. DONE outcomes
are silently skipped — this corpus is specifically for learning from failures.

Schema per line: ts, ticket_id, tags, size, task_class, signal,
advisor_signal?, iterations, cost_usd, tokens_in, tokens_out, excerpt.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_CORPUS = (
    Path(os.environ.get("GRANNY_HOME", str(Path.home() / ".granny")))
    / "escalation_corpus.jsonl"
)
_lock = threading.Lock()


def append_outcome(
    ticket: dict,
    signal: str,
    *,
    advisor_signal: str | None = None,
    task_class: str = "worker",
    iterations: int = 0,
    cost_usd: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    excerpt: str = "",
    corpus_path: Path | None = None,
) -> None:
    """Append one non-DONE dispatch outcome to the escalation corpus.

    No-ops on DONE signal — caller doesn't need to pre-filter.
    Never raises; write failures are logged at WARNING.
    """
    if signal == "DONE":
        return

    path = corpus_path or _DEFAULT_CORPUS
    path.parent.mkdir(parents=True, exist_ok=True)

    entry: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticket_id": ticket.get("id", ""),
        "tags": list(ticket.get("tags", [])),
        "size": ticket.get("size", "?"),
        "task_class": task_class,
        "signal": signal,
        "iterations": iterations,
        "cost_usd": cost_usd,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "excerpt": excerpt[:300],
    }
    if advisor_signal:
        entry["advisor_signal"] = advisor_signal

    with _lock:
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            log.warning("escalation_corpus: write failed: %s", e)
            return

    log.info(
        "escalation_corpus: appended ticket=%s task_class=%s signal=%s advisor=%s",
        ticket.get("id", ""),
        task_class,
        signal,
        advisor_signal,
    )

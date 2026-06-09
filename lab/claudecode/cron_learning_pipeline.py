#!/usr/bin/env python3
"""cron_learning_pipeline.py — nightly learning pipeline run.

Scheduled at 3:30 AM daily by cron. Calls LearningPipeline.run_once() to
consume the inference learning queue and write epistemic knowledge nodes.

Logs to ~/.unseen_university/logs/learning_pipeline.log.

Cron entry (add via `crontab -e`):
    30 3 * * * /home/akien/dev/src/UnseenUniversity/.venv/bin/python3 \\
        /home/akien/dev/src/UnseenUniversity/lab/claudecode/cron_learning_pipeline.py \\
        >> ~/.unseen_university/logs/learning_pipeline.log 2>&1
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)

_log = logging.getLogger("cron_learning_pipeline")


def main() -> int:
    _log.info("learning_pipeline: nightly run starting")

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )

    try:
        from devices.librarian.learning_pipeline import LearningPipeline
        pipeline = LearningPipeline(db_url)
        stats = pipeline.run_once()
    except Exception as e:
        _log.error("learning_pipeline: run_once() failed: %s", e)
        return 1

    if "error" in stats:
        _log.error("learning_pipeline: run_once returned error: %s", stats["error"])
        return 1

    _log.info(
        "learning_pipeline: complete — entries_processed=%d nodes_built=%d",
        stats.get("entries_processed", 0),
        stats.get("nodes_built", 0),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

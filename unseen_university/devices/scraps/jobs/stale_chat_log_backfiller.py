"""
StaleChatLogBackfiller — keep today's CC chat mirror fresh under ~/.unseen_university/logs/CC.0/.

Runs cc_log_stop_hook.py every 5min — scans all project dirs so ADC sessions are included.
Historical files are not rebuilt here; that's a /day-close concern.

This is a Scraps maintenance job (non-inference). It is scheduled by the Scraps
job-runner daemon (devices/scraps/daemon.py), which the Ground Loop supervises —
so it runs independently of whether Igor's cognition is up. Relocated out of
Igor's push_sources by T-stale-chat-backfiller-relocate (for real this time).
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class StaleChatLogBackfiller:
    """
    Keep today's CC chat mirror fresh under ~/.unseen_university/logs/CC.0/.

    Runs cc_log_stop_hook.py every 5min — scans all project dirs so ADC
    sessions are included. Historical files are not rebuilt here; that's
    a /day-close concern.
    """

    name = "stale_chat_log_backfiller"
    TIMING_TIER = "slow"
    REFRESH_INTERVAL_SEC = 300  # T-cc-mirror-5min-today

    def __init__(self):
        self._last_run: Optional[datetime] = None

    def run(self):
        """
        Run the backfill process if the interval has elapsed.
        """
        now = datetime.now(timezone.utc)
        if (
            self._last_run is not None
            and (now - self._last_run).total_seconds() < self.REFRESH_INTERVAL_SEC
        ):
            return
        self._last_run = now

        try:
            import subprocess

            from unseen_university._uu_root import uu_root

            # Use cc_log_stop_hook.py — scans all project dirs (not just TheIgors),
            # so ADC-project CC sessions are included. export_chat.py only scanned
            # the TheIgors project and smashed the log when ADC became primary.
            root = Path(uu_root())
            result = subprocess.run(
                [
                    "python3",
                    str(root / "devlab" / "claudecode" / "cc_log_stop_hook.py"),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(root),
            )

            if result.returncode == 0:
                print(f"CHAT_LOG_EXPORT|status=success|timestamp={now.isoformat()}")
            else:
                print(f"CHAT_LOG_EXPORT|status=failed|timestamp={now.isoformat()}")
        except Exception as exc:
            print(f"CHAT_LOG_EXPORT|status=error|timestamp={now.isoformat()}|error={exc}")


def main():
    """
    Entry point for running the backfiller as a standalone script.
    """
    backfiller = StaleChatLogBackfiller()
    backfiller.run()


if __name__ == "__main__":
    main()

"""Entry point: python -m unseen_university.devices.scraps

Brings Scraps up the aider way (ONE daemon structure — no standalone daemon, no
Ground-Loop subprocess): construct the device (which installs the canonical per-device
JSON log sink via DiagnosticBase), start its shim (which owns the in-process job-runner
loop thread), and park. No ``while True`` run_loop, no PID file, no signal-handled
subprocess here.
"""

import logging
import signal
import sys

from unseen_university.devices.scraps.scraps_device import ScrapsDevice

log = logging.getLogger(__name__)


def main() -> None:
    device = ScrapsDevice()
    device._shim.start()
    log.info("Scraps: started — job-runner loop active (Ctrl+C to stop)")

    def _shutdown(sig, _frame):
        log.info("Scraps: shutdown signal received")
        device._shim.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    signal.pause()


if __name__ == "__main__":
    main()

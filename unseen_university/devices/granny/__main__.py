"""Entry point: python -m unseen_university.devices.granny

Brings Granny up the aider way (ONE daemon structure — no standalone daemon):
construct the device (which installs the canonical per-device JSON log sink via
DiagnosticBase), start its shim, and park. The shim owns the queue-watch watchdog;
when sprint work is pending the watchdog demand-starts Granny's in-process dispatch
loop thread. No ``while True`` run_loop, no PID file, no tmux subprocess here.
"""

import logging
import signal
import sys

from unseen_university.devices.granny.device import GrannyWeatherwaxDevice

log = logging.getLogger(__name__)


def main() -> None:
    device = GrannyWeatherwaxDevice()
    device._shim.start()
    log.info("Granny: started — watching the queue for sprint work (Ctrl+C to stop)")

    def _shutdown(sig, _frame):
        log.info("Granny: shutdown signal received")
        device._shim.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    signal.pause()


if __name__ == "__main__":
    main()

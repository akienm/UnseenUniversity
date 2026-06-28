"""Entry point: python -m unseen_university.devices.dicksimnel"""

import logging
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from unseen_university.devices.dicksimnel.device import DickSimnelDevice

log = logging.getLogger(__name__)


def main() -> None:
    device = DickSimnelDevice()
    device._shim.start()
    log.info("DickSimnel: started — waiting for tickets (Ctrl+C to stop)")

    def _shutdown(sig, frame):
        log.info("DickSimnel: shutdown signal received")
        device._shim.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Block forever; shim poll loop runs in background thread
    signal.pause()


if __name__ == "__main__":
    main()

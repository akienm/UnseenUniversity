"""Entry point: python -m unseen_university.devices.aider"""

import logging
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from unseen_university.devices.aider.device import AiderDevice

log = logging.getLogger(__name__)


def main() -> None:
    from unseen_university.devices.pool import wipe_ephemeral_instance_dir
    device = AiderDevice()
    wipe_ephemeral_instance_dir(device.instance_abbreviation, device.instance_number)
    device._shim.start()
    log.info("Aider: started — waiting for tickets (Ctrl+C to stop)")

    def _shutdown(sig, frame):
        log.info("Aider: shutdown signal received")
        device._shim.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    signal.pause()


if __name__ == "__main__":
    main()

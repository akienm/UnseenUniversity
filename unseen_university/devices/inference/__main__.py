"""Entry point: python -m unseen_university.devices.inference

Runs the inference proxy shim — manages the backend AND, when AIDER_PROXY_PORT is set,
serves the limited Ollama-compatible HTTP door for aider (T-aider-through-inference-proxy).
Point aider's OLLAMA_API_BASE at http://127.0.0.1:$AIDER_PROXY_PORT and it routes through
InferenceDevice.dispatch (tier→source, cloud escalation, budget-ledger cost, io_corpus).
"""

import logging
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from unseen_university.devices.inference.shim import InferenceShim

log = logging.getLogger(__name__)


def main() -> None:
    shim = InferenceShim()
    if not shim.start():
        log.error("inference shim failed to start")
        sys.exit(1)
    log.info("inference proxy running — Ctrl+C to stop")

    def _shutdown(sig, frame):
        log.info("inference proxy: shutdown signal received")
        shim.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    signal.pause()


if __name__ == "__main__":
    main()

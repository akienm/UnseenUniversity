"""
DeviceQueue — bounded per-device inbound message buffer.

Messages sent to a device that is mid-restart are held here rather than dropped
or causing the sender to block. On restart completion the skeleton calls drain()
before forwarding new messages, preserving delivery order.

Storage: in-memory deque. No persistence — a skeleton restart loses queued
messages, but the window is small (same as restart duration).

Drop policy (from DeviceConfig):
    drop_newest=False (default/rack default): drop the oldest message to make room.
        Appropriate for state/status traffic where newer supersedes older.
    drop_newest=True: drop the incoming message.
        Appropriate for ordered pipelines where every message must be processed
        in sequence and losing any message is worse than back-pressure.
"""

from __future__ import annotations

import logging
from collections import deque

from bus.envelope import Envelope
from config.device_config import DeviceConfig

log = logging.getLogger(__name__)


class DeviceQueue:
    def __init__(self, device_id: str, config: DeviceConfig | None = None) -> None:
        self._device_id = device_id
        self._config = config or DeviceConfig()
        self._queue: deque[Envelope] = deque()

    def enqueue(self, envelope: Envelope) -> None:
        if len(self._queue) >= self._config.max_queue_length:
            if self._config.drop_newest:
                log.warning(
                    "queue full for %s; dropping newest (drop_newest=True)",
                    self._device_id,
                )
                return
            else:
                dropped = self._queue.popleft()
                log.warning(
                    "queue full for %s; dropping oldest (from=%s sent_at=%s)",
                    self._device_id,
                    dropped.from_device,
                    dropped.sent_at,
                )
        self._queue.append(envelope)

    def drain(self) -> list[Envelope]:
        """Return all queued messages in order and clear the queue."""
        messages = list(self._queue)
        self._queue.clear()
        return messages

    def __len__(self) -> int:
        return len(self._queue)

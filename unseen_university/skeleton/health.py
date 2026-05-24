"""
Rack-level health and enumeration functions.

These are the implementations behind the rack.* MCP tools. Extracted from
Skeleton to keep the MCP registration code thin and these functions directly
testable without going through FastMCP.

Health rationale: no background heartbeat. Health is a question, not a process.
Poll when you need the answer; callers decide how often. On-demand parallel
fan-out with asyncio.gather() + per-device timeout avoids cascading delays when
a device is slow or unreachable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from unseen_university.device import BaseDevice

if TYPE_CHECKING:
    from bus.imap_server import IMAPServer
    from skeleton.registry import DeviceRegistry

log = logging.getLogger(__name__)

_DEFAULT_HEALTH_TIMEOUT = 5.0


def rack_devices(registry: "DeviceRegistry") -> list[dict]:
    """
    Return all registered device records.

    Shape: [{device_id, name, status, mailbox, registered_at, config}, ...]
    """
    return registry.list_devices()


def rack_channels(imap_server: "IMAPServer") -> list[str]:
    """
    Return all IMAP mailbox names currently registered on this rack.

    Shape: ['Shared', 'CC.0', 'igor-wild-0001', ...]
    """
    return imap_server.list_mailboxes()


async def rack_health_async(
    devices: dict[str, BaseDevice],
    timeout: float = _DEFAULT_HEALTH_TIMEOUT,
) -> dict:
    """
    Parallel health fan-out across all live devices.

    Each device.health() runs concurrently via asyncio.to_thread() (health()
    methods are synchronous). Devices that exceed `timeout` seconds are reported
    as unhealthy with error='timeout'; devices that raise are reported with
    error=str(exception).

    Shape: {device_id: {healthy: bool, details: dict, error: str|None}}
    """

    async def _check(device_id: str, device: BaseDevice) -> tuple[str, dict]:
        try:
            details = await asyncio.wait_for(
                asyncio.to_thread(device.health),
                timeout=timeout,
            )
            healthy = details.get("status") in ("healthy", "degraded")
            return device_id, {"healthy": healthy, "details": details, "error": None}
        except asyncio.TimeoutError:
            log.warning(
                "health check timed out for %s (timeout=%.1fs)", device_id, timeout
            )
            return device_id, {"healthy": False, "details": {}, "error": "timeout"}
        except Exception as exc:
            log.warning("health check raised for %s: %s", device_id, exc)
            return device_id, {"healthy": False, "details": {}, "error": str(exc)}

    if not devices:
        return {}

    results = await asyncio.gather(*[_check(did, dev) for did, dev in devices.items()])
    return dict(results)


def rack_health_sync(
    devices: dict[str, BaseDevice],
    timeout: float = _DEFAULT_HEALTH_TIMEOUT,
) -> dict:
    """
    Synchronous wrapper around rack_health_async for use in non-async contexts.

    Creates a new event loop when not already inside one (safe from MCP tool
    callbacks which FastMCP calls from its own event loop as async tasks).
    When already in a running loop (e.g. called from an async test or MCP tool),
    use rack_health_async directly instead.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # Caller is already inside an event loop — they should call rack_health_async.
        # Fall back to a thread executor to avoid "cannot run nested event loops".
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(lambda: asyncio.run(rack_health_async(devices, timeout)))
            return future.result(timeout=timeout + 1)
    else:
        return asyncio.run(rack_health_async(devices, timeout))

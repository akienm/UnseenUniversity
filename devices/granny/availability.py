"""
Generic availability semaphore for Granny and other devices.

Provides is_available(worker_id) function that reads
~/.granny/available/{worker_id}.available.{true,false} files to determine
availability status. The .false file takes precedence over .true.

Usage:
    from devices.granny.availability import is_available

    if is_available('CC.0'):
        # do something
"""

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_AVAILABLE_DIR = Path(
    os.environ.get("GRANNY_AVAIL_DIR", str(Path.home() / ".granny" / "available"))
)


def _avail_dir() -> Path:
    d = _AVAILABLE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_available(worker_id: str) -> bool:
    """Return True when worker is available: .true present and .false absent.

    Protocol: {worker_id}.available.false blocks regardless; {worker_id}.available.true
    opts in. Neither file = unavailable. .false wins over .true.
    """
    d = _avail_dir()
    if (d / f"{worker_id}.available.false").exists():
        log.debug("availability: %s blocked (.false present)", worker_id)
        return False
    if (d / f"{worker_id}.available.true").exists():
        log.debug("availability: %s available (.true present)", worker_id)
        return True
    log.debug("availability: %s unavailable (no .true file)", worker_id)
    return False


def mark_available(worker_id: str) -> None:
    """Drop the .true flag; remove .false if present."""
    d = _avail_dir()
    (d / f"{worker_id}.available.false").unlink(missing_ok=True)
    (d / f"{worker_id}.available.true").touch()
    log.info("availability: %s marked available", worker_id)


def mark_unavailable(worker_id: str) -> None:
    """Drop the .false flag; remove .true if present."""
    d = _avail_dir()
    (d / f"{worker_id}.available.true").unlink(missing_ok=True)
    (d / f"{worker_id}.available.false").touch()
    log.info("availability: %s marked unavailable", worker_id)


def clear_worker_state(worker_id: str) -> None:
    """Remove both flags for this worker."""
    d = _avail_dir()
    (d / f"{worker_id}.available.false").unlink(missing_ok=True)
    (d / f"{worker_id}.available.true").unlink(missing_ok=True)
    log.info("availability: %s state cleared", worker_id)

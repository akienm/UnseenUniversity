"""
Acquisition channel framework (D230, D231).

Channels wrap content acquisition tools with:
  - Uniform interface: acquire(query) -> AcquireResult | ChannelFailure
  - Constraints (e.g., "NO account creation")
  - Cost model (per-call USD + reliability tier)
  - Retry policy
  - Short-circuit flag (if True, a hit stops further channel search)

Pipeline flow:
  1. FileInboxChannel (short_circuits=True) — explicit dropped files
  2. DirectURLChannel (short_circuits=True) — explicit URL or file path
  3. CalibreChannel (local EPUB search)
  4. GeminiSearchChannel (free web search via browser profile)
  5. BrowserUseChannel (structured acquisition with constraints)

Registry handles channel registration, discovery, and ordered access.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

from ...igor_base import IgorBase

# Lazy imports for circular dependency avoidance
import sys
from importlib import import_module

# ── Data Structures ────────────────────────────────────────────────────────


class ChannelReliability(str, Enum):
    """Reliability tier for cost and fallback decisions."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class AcquireRequest:
    """Request to acquire content."""

    query: str  # Search term, URL, file path, or book title
    context: dict = field(
        default_factory=dict
    )  # Extra metadata (e.g., {"source": "habit"})


@dataclass
class BlobMeta:
    """Metadata about acquired content."""

    title: str
    source: str  # Channel name that provided it
    url: Optional[str] = None  # If sourced from web
    file_path: Optional[str] = None  # If sourced from file
    format: str = "unknown"  # epub, pdf, html, text, etc.
    size_bytes: Optional[int] = None
    retrieved_at: Optional[str] = None  # ISO 8601 timestamp


@dataclass
class AcquireResult:
    """Successful acquisition result."""

    blob: bytes  # File content
    meta: BlobMeta  # Metadata
    cost_usd: float = 0.0


@dataclass
class ChannelFailure:
    """Channel failure result."""

    channel_name: str
    reason: str
    cost_usd: float = 0.0
    retry_in_seconds: Optional[float] = None  # Suggested retry delay


# ── Channel Base Class ────────────────────────────────────────────────────


class Channel(ABC, IgorBase):
    """
    Abstract acquisition channel.

    Each channel implements a single acquire method that maps a query
    to content or reports failure.

    Inherits IgorBase: self.log routes to igor.network.* hierarchy automatically.
    """

    def __init__(
        self,
        name: str,
        constraints: list[str],
        cost_per_call_usd: float,
        reliability: ChannelReliability,
        one_way: bool = False,
        short_circuits: bool = False,
        max_attempts: int = 1,
        backoff_sec: float = 1.0,
    ):
        """
        Initialize a channel.

        name: Channel identifier
        constraints: List of limitations (e.g., ["NO account creation"])
        cost_per_call_usd: Cost per call (0.0 for free)
        reliability: HIGH | MEDIUM | LOW
        one_way: If True, channel is only usable once per query (e.g., Calibre exhausts local results)
        short_circuits: If True, a successful hit stops further channel search
        max_attempts: Retry count
        backoff_sec: Backoff seconds between retries
        """
        IgorBase.__init__(self)
        self.name = name
        self.constraints = constraints
        self.cost_per_call_usd = cost_per_call_usd
        self.reliability = reliability
        self.one_way = one_way
        self.short_circuits = short_circuits
        self.max_attempts = max_attempts
        self.backoff_sec = backoff_sec

    @abstractmethod
    def acquire(self, request: AcquireRequest) -> AcquireResult | ChannelFailure:
        """
        Attempt to acquire content for the given request.

        Returns AcquireResult on success or ChannelFailure on any problem.
        """
        pass

    def to_dict(self) -> dict:
        """Serializable channel description."""
        return {
            "name": self.name,
            "constraints": self.constraints,
            "cost_per_call_usd": self.cost_per_call_usd,
            "reliability": self.reliability.value,
            "one_way": self.one_way,
            "short_circuits": self.short_circuits,
            "max_attempts": self.max_attempts,
            "backoff_sec": self.backoff_sec,
        }


# ── Channel Registry ──────────────────────────────────────────────────────


class ChannelRegistry:
    """
    Manages channel registration and ordered access.

    Channels are tried in registration order until one succeeds (or short-circuits).
    """

    def __init__(self):
        self._channels: list[Channel] = []
        self._by_name: dict[str, Channel] = {}

    def register(self, channel: Channel) -> None:
        """Register a new channel. Registration order = search order."""
        if channel.name in self._by_name:
            raise ValueError(f"Channel '{channel.name}' already registered")
        self._channels.append(channel)
        self._by_name[channel.name] = channel

    def get(self, name: str) -> Optional[Channel]:
        """Get a channel by name."""
        return self._by_name.get(name)

    def list_channels(self) -> list[Channel]:
        """Get all registered channels in order."""
        return list(self._channels)

    def acquire(
        self, request: AcquireRequest, skip_channels: Optional[set[str]] = None
    ) -> tuple[AcquireResult | ChannelFailure, str]:
        """
        Try to acquire content using registered channels in order.

        Returns (result, channel_name_used).
        If all channels fail, returns the last failure.
        """
        skip = skip_channels or set()
        last_failure = None

        for channel in self._channels:
            if channel.name in skip:
                continue

            try:
                result = channel.acquire(request)
                if isinstance(result, AcquireResult):
                    # Success!
                    return result, channel.name
                else:
                    # Failure
                    last_failure = result
                    # If short_circuits is set on failure, stop trying other channels
                    # (No — short_circuits only applies on success)
            except Exception as e:
                # Unexpected error — treat as failure, continue
                last_failure = ChannelFailure(
                    channel_name=channel.name,
                    reason=f"Exception: {type(e).__name__}: {str(e)[:200]}",
                    cost_usd=0.0,
                )

        # All channels failed
        if last_failure:
            return last_failure, last_failure.channel_name
        return (
            ChannelFailure(
                channel_name="(none)",
                reason="No channels available",
                cost_usd=0.0,
            ),
            "(none)",
        )

    def to_dict(self) -> dict:
        """Serializable registry description."""
        return {
            "channels": [ch.to_dict() for ch in self._channels],
            "count": len(self._channels),
        }


# ── Module-level registry singleton ───────────────────────────────────────

_default_registry: Optional[ChannelRegistry] = None


def get_registry() -> ChannelRegistry:
    """Get the default channel registry. Instantiates on first call."""
    global _default_registry
    if _default_registry is None:
        _default_registry = ChannelRegistry()
    return _default_registry


# ── Auto-bootstrap on module import ────────────────────────────────────────
# Defer bootstrap to avoid circular imports. It will run the first time
# get_registry() is called and finds an empty registry.


def _ensure_bootstrapped() -> None:
    """Bootstrap channels if not already done."""
    registry = get_registry()
    if len(registry.list_channels()) == 0:
        # Not bootstrapped yet — import and run bootstrap
        try:
            from . import bootstrap  # noqa: F401
        except Exception as _exc:
            # Bootstrap failed silently (e.g., import error); channels stay empty
            from ..cognition.forensic_logger import log_error as _le
            _le(kind="SILENT_EXCEPT", detail=f"__init__.py:266: {_exc}")


_ensure_bootstrapped()

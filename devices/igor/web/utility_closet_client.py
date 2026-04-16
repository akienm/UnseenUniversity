"""
Utility closet client — D335 Phase 2.

Thin REST client that Igor uses to register with the utility closet platform.
All methods are fire-and-forget with logging — never block Igor's main loop.

Usage in main.py:
    from .web.utility_closet_client import uc_client
    uc_client.register("igor", capabilities=["chat", "tools", "habits"])
    uc_client.push_stats(stats_dict)
    uc_client.send_message("hello", session_id="shared")
    uc_client.deregister()
"""

import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Optional
from ..igor_base import IgorBase

log = logging.getLogger(__name__)

# UC may serve HTTPS on the main port (8080) with a self-signed/locally-trusted
# cert, and plain HTTP on the companion port (default 8082). We default to the
# plain-HTTP companion to avoid TLS validation against locally-trusted CAs that
# Python's default trust store doesn't know about. Override with IGOR_UC_BASE
# (e.g. "https://localhost:8080") if you want to force HTTPS.
_UC_PORT = int(os.environ.get("IGOR_UC_PORT", "8082"))
_UC_BASE = os.environ.get("IGOR_UC_BASE", f"http://localhost:{_UC_PORT}")

# For HTTPS to localhost we accept any cert — UC is on the same machine, the
# trust boundary is the loopback interface, not the cert chain.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _open(req: urllib.request.Request, timeout: float):
    if req.full_url.startswith("https://"):
        return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)
    return urllib.request.urlopen(req, timeout=timeout)


def _post(path: str, body: dict, timeout: float = 5.0) -> Optional[dict]:
    """POST JSON to utility closet. Returns response dict or None on failure."""
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{_UC_BASE}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _open(req, timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        log.debug("utility closet POST %s failed (URLError): %s", path, e)
        return None
    except Exception as e:
        log.debug("utility closet POST %s failed: %s", path, e)
        return None


def _get(path: str, timeout: float = 5.0) -> Optional[dict]:
    """GET from utility closet. Returns response dict or None on failure."""
    try:
        req = urllib.request.Request(f"{_UC_BASE}{path}", method="GET")
        with _open(req, timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.debug("utility closet GET %s failed: %s", path, e)
        return None


class UtilityClosetClient(IgorBase):
    """Client for communicating with the utility closet platform server."""

    def __init__(self):
        self._agent_id: Optional[str] = None
        self._registered = False
        self._stats_thread: Optional[threading.Thread] = None
        self._stats_interval: float = 5.0  # seconds between stats pushes
        self._stop_event = threading.Event()
        self._stats_fn = None

    @property
    def is_registered(self) -> bool:
        return self._registered

    @property
    def agent_id(self) -> Optional[str]:
        return self._agent_id

    def is_available(self) -> bool:
        """Check if the utility closet is running and healthy."""
        health = _get("/health", timeout=2.0)
        return health is not None and health.get("status") == "ok"

    def register(
        self,
        agent_id: str,
        capabilities: Optional[list] = None,
        callback_url: str = "",
    ) -> bool:
        """Register this agent with the utility closet.

        Returns True if registration succeeded, False otherwise.
        Non-blocking — logs warning on failure but does not raise.
        """
        if not self.is_available():
            log.info(
                "Utility closet not available — running without platform registration"
            )
            return False

        result = _post(
            "/api/agents/register",
            {
                "agent_id": agent_id,
                "capabilities": capabilities or [],
                "callback_url": callback_url,
            },
        )

        if result and result.get("status") == "ok":
            self._agent_id = agent_id
            self._registered = True
            log.info("Registered with utility closet as '%s'", agent_id)
            return True
        else:
            log.warning("Failed to register with utility closet: %s", result)
            return False

    def deregister(self) -> bool:
        """Deregister this agent from the utility closet.

        Called during shutdown. Best-effort — never raises.
        """
        if not self._registered or not self._agent_id:
            return True

        self._stop_event.set()

        result = _post("/api/agents/deregister", {"agent_id": self._agent_id})
        self._registered = False

        if result and result.get("status") == "ok":
            log.info("Deregistered from utility closet")
            return True
        else:
            log.debug("Deregister failed (non-fatal): %s", result)
            return False

    def push_stats(self, stats: dict) -> bool:
        """Push dashboard stats to the utility closet. Non-blocking."""
        if not self._registered or not self._agent_id:
            return False
        result = _post(f"/api/agents/{self._agent_id}/stats", stats)
        return result is not None and result.get("status") == "ok"

    def send_message(self, content: str, session_id: str = "shared") -> bool:
        """Send a message through the utility closet to web clients."""
        if not self._registered or not self._agent_id:
            return False
        result = _post(
            f"/api/agents/{self._agent_id}/send",
            {
                "content": content,
                "session_id": session_id,
            },
        )
        return result is not None and result.get("status") == "ok"

    def poll_messages(self) -> list:
        """Poll for incoming messages from the utility closet.

        Returns list of message dicts, or empty list on failure.
        """
        if not self._registered or not self._agent_id:
            return []
        result = _get(f"/api/agents/{self._agent_id}/poll", timeout=2.0)
        if result and "messages" in result:
            return result["messages"]
        return []

    def start_stats_pusher(self, stats_fn, interval: float = 5.0):
        """Start a background thread that periodically pushes stats.

        stats_fn: callable() -> dict (e.g. Igor.get_stats)
        interval: seconds between pushes (default 5)
        """
        if not self._registered:
            return

        self._stats_fn = stats_fn
        self._stats_interval = interval
        self._stop_event.clear()

        def _push_loop():
            while not self._stop_event.is_set():
                try:
                    stats = self._stats_fn()
                    self.push_stats(dict(stats))
                except Exception as e:
                    log.debug("stats push error (non-fatal): %s", e)
                self._stop_event.wait(self._stats_interval)

        self._stats_thread = threading.Thread(
            target=_push_loop, daemon=True, name="uc-stats-pusher"
        )
        self._stats_thread.start()
        log.info("Stats pusher started (interval=%ss)", interval)


# Module-level singleton
uc_client = UtilityClosetClient()

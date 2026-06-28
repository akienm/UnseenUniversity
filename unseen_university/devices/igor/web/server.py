"""
web/server.py — Thin facade over adc_client.

Igor no longer runs its own web server. All HTTP, WebSocket, and UI serving
is handled by the ADC web_server device on port 8080. This module preserves
the legacy API (`start`, `send`, `broadcast_activity`, `broadcast_name_resolved`,
`incoming`) so main.py and listener.py don't need to change.

Outbound: send() forwards through uc_client.send_message() to ADC.
Inbound: a background polling thread drains uc_client.poll_messages() into
  the `incoming` queue, where listener._poll_web picks it up as before.
Activity/name broadcasts: passed through to ADC via new uc_client methods
  (fire-and-forget — if ADC isn't up, they're silently dropped).
"""

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .adc_client import uc_client

log = logging.getLogger(__name__)


# ── NetworkMessage: DTO consumed by main._drain_network ───────────────────────
@dataclass
class NetworkMessage:
    source: str
    content: str
    author: str
    reply_info: dict = field(default_factory=dict)
    raw: Any = None
    received_at: float = 0.0


# ── Public API: the queue _drain_network drains ───────────────────────────────
incoming: queue.Queue = queue.Queue()

# ── Shared state refs (set by start(); used by stats pusher) ──────────────────
_stats_fn = None
_cortex_fn = None
_igor_fn = None

# ── Latest broadcast state — rolled into stats for UC dashboard ───────────────
_activity_state: dict = {}
_name_resolved: Optional[str] = None
_state_lock = threading.Lock()

# ── Poller thread ─────────────────────────────────────────────────────────────
_poll_thread: Optional[threading.Thread] = None
_poll_stop = threading.Event()
_POLL_INTERVAL = float(os.environ.get("IGOR_UC_POLL_INTERVAL", "1.0"))


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_EPOCH_CHECK_INTERVAL = 10  # polls between ADC restart checks


def _poll_loop():
    """Background: drain UC's poll endpoint into the incoming queue."""
    poll_count = 0
    while not _poll_stop.is_set():
        try:
            if uc_client.is_registered:
                msgs = uc_client.poll_messages()
                for m in msgs:
                    # UC returns messages already shaped {content, author, session_id, ...}
                    # Listener expects dict with at least content, author, session_id.
                    incoming.put(
                        {
                            "content": m.get("content", ""),
                            "author": m.get("author", "web-user"),
                            "session_id": m.get("session_id", "shared"),
                            "client_id": m.get("client_id"),
                        }
                    )
            poll_count += 1
            if poll_count % _EPOCH_CHECK_INTERVAL == 0:
                uc_client.check_server_epoch()
        except Exception as e:
            log.debug("uc poll error (non-fatal): %s", e)
        _poll_stop.wait(_POLL_INTERVAL)


def start(stats_fn=None, cortex_fn=None, igor_fn=None) -> None:
    """Legacy signature. No HTTP server is started — UC owns that now.

    What this does now:
      1. Stores fn refs so the UC stats pusher can reach Igor state.
      2. Registers Igor with UC so send/poll are unblocked.
      3. Starts a background thread that polls UC for incoming messages.

    Returns immediately.
    """
    global _stats_fn, _cortex_fn, _igor_fn, _poll_thread

    _stats_fn = stats_fn
    _cortex_fn = cortex_fn
    _igor_fn = igor_fn

    if not uc_client.is_registered:
        uc_client.register("igor", capabilities=["chat", "tools", "habits"])

    if _poll_thread is not None and _poll_thread.is_alive():
        return  # already started

    _poll_stop.clear()
    _poll_thread = threading.Thread(
        target=_poll_loop, daemon=True, name="uc-incoming-poll"
    )
    _poll_thread.start()
    log.info("web/server: facade mode — UC (8080) owns HTTP/WS; polling for incoming")


def stop() -> None:
    """Stop the polling thread. Called on shutdown."""
    _poll_stop.set()


def send(text: str, session_id: str = "shared") -> bool:
    """Forward an outbound reply through UC to web clients on the given session.

    Returns True on successful delivery, False on any failure. Prior to
    2026-04-19 this was fire-and-forget with DEBUG-level error logs,
    which silently dropped ~60% of Igor's longer replies (T-web-chat-reply-
    not-surfacing). Now logs at WARNING on any failure so 'reply present
    in console, missing in web' is observable from tools.log.
    """
    log.info(
        "web_send: interface crossing session=%s len=%d: %s",
        session_id,
        len(text or ""),
        (text or "")[:80].replace("\n", " "),
    )
    try:
        ok = uc_client.send_message(text, session_id=session_id)
        if not ok:
            log.warning(
                "uc send returned False (session=%s len=%d head=%r) — reply dropped",
                session_id,
                len(text or ""),
                (text or "")[:80],
            )
        return bool(ok)
    except Exception as e:
        log.warning(
            "uc send raised (session=%s len=%d): %s",
            session_id,
            len(text or ""),
            e,
        )
        return False


def broadcast_activity(state: dict) -> None:
    """Store the latest activity state. Pushed to UC via the stats pusher."""
    global _activity_state
    with _state_lock:
        _activity_state = dict(state)
    # Fire-and-forget push — UC uses the latest stats for dashboard rendering.
    try:
        if uc_client.is_registered:
            uc_client.push_stats({"activity": dict(state), "ts": _ts()})
    except Exception as e:
        log.debug("uc activity push error (non-fatal): %s", e)


def broadcast_name_resolved(name: str) -> None:
    """Record the resolved user name. Pushed via stats for the UI to pick up."""
    global _name_resolved
    with _state_lock:
        _name_resolved = name
    try:
        if uc_client.is_registered:
            uc_client.push_stats({"name_resolved": name, "ts": _ts()})
    except Exception as e:
        log.debug("uc name push error (non-fatal): %s", e)


def get_activity_state() -> dict:
    """Return the last broadcast activity state (used by stats rollup)."""
    with _state_lock:
        return dict(_activity_state)


def get_name_resolved() -> Optional[str]:
    with _state_lock:
        return _name_resolved


# ── System health endpoint (#232) ─────────────────────────────────────────────
# Retained as a standalone handler so tests can mount it and UC can optionally
# proxy it. Not registered by this module — lives here for backward compat.


async def _api_system_health(request):
    """GET /api/system_health — cluster resource visibility (#232).

    Returns machine health / load / in-use state for the dashboard UI.
    Originally served by Igor's web server; now a standalone handler that
    can be registered by UC or called directly from tests.
    """
    from starlette.responses import JSONResponse

    try:
        from ..tools.machine_manager import get_ranked_machines, is_in_use
        from ..cognition.cluster_router import _health_cache, _health_lock

        override = os.environ.get("IGOR_INFERENCE_OVERRIDE", "") or None
        machines = []
        for m in get_ranked_machines():
            with _health_lock:
                cached = _health_cache.get(m.ollama_host)
            healthy = cached[0] if cached is not None else None
            machines.append(
                {
                    "hostname": m.hostname,
                    "display_name": m.display_name,
                    "ollama_host": m.ollama_host,
                    "healthy": healthy,
                    "in_use": is_in_use(m.hostname),
                    "inference_rank": m.inference_rank,
                    "model": m.ollama_model,
                    "is_local": m.is_local,
                    "network_type": m.network_type,
                    "ram_gb": m.ram_gb,
                    "status": m.status,
                }
            )
        return JSONResponse({"ts": _ts(), "override": override, "machines": machines})
    except Exception as e:
        log.warning("_api_system_health error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)

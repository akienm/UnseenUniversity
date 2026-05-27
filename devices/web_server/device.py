"""
WebServerDevice — rack device for the unseen_university web server.

Manages the Starlette/uvicorn web server (server.py) as a subprocess.
Provides the web UI, WebSocket hub, agent registration, and dashboard API
Manages the ADC web server subprocess.

Port: ADC_WEB_PORT env var (falls back to IGOR_UC_PORT), default 8080.
PID file: ~/.unseen_university/web_server.pid
# tags: Infrastructure, Platform
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from unseen_university.device import BaseDevice, INTERFACE_VERSION

log = logging.getLogger(__name__)

_START_TIME = time.time()
_RUNTIME_ROOT = Path(
    os.environ.get("ADC_RUNTIME_ROOT")
    or os.environ.get("IGOR_RUNTIME_ROOT")
    or Path.home() / ".unseen_university"
)
_PID_FILE = _RUNTIME_ROOT / "web_server.pid"
_LOG_FILE = _RUNTIME_ROOT / "logs" / "web_server.log"
_SERVER_PY = Path(__file__).parent / "server.py"
_PORT = int(os.environ.get("ADC_WEB_PORT") or os.environ.get("IGOR_UC_PORT", "8080"))
_HTTP_PORT = int(
    os.environ.get("ADC_WEB_HTTP_PORT") or os.environ.get("IGOR_UC_HTTP_PORT", "8082")
)
_HEALTH_TIMEOUT = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_health() -> dict | None:
    """Return /health JSON dict if server is responding, None otherwise.

    Tries HTTPS on _PORT first (server enables SSL when mkcert is available),
    then plain HTTP on _PORT, then plain HTTP on _HTTP_PORT fallback.
    """
    import ssl as _ssl

    urls = []
    if (
        os.environ.get("IGOR_SSL_CERT")
        or (Path.home() / ".unseen_university" / "certs" / "localhost+3.pem").exists()
    ):
        urls.append(f"https://localhost:{_PORT}/health")
    urls.append(f"http://localhost:{_PORT}/health")
    urls.append(f"http://localhost:{_HTTP_PORT}/health")

    for url in urls:
        try:
            ctx = None
            if url.startswith("https://"):
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
            with urllib.request.urlopen(
                url, timeout=_HEALTH_TIMEOUT, context=ctx
            ) as resp:
                data = json.loads(resp.read())
                if data.get("status") == "ok":
                    return data
        except Exception:
            pass
    return None


class WebServerDevice(BaseDevice):
    """
    Rack device that manages the unseen_university web server subprocess.

    The server exposes the web UI, WebSocket hub, /api/agents/* registration
    endpoints, and the /health + /metrics platform APIs. Agents (Igor, CC)
    register themselves via POST /api/agents/register.
    """

    DEVICE_ID = "web-server"

    def __init__(self) -> None:
        super().__init__()
        self._proc: subprocess.Popen | None = None
        self._blocked = False
        self._block_reason = ""
        self._startup_errors: list[str] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the web server subprocess if not already running."""
        if _check_health():
            log.info("[web-server] already running on port %d", _PORT)
            return
        _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        (_RUNTIME_ROOT / "logs").mkdir(parents=True, exist_ok=True)
        try:
            log_fp = open(_LOG_FILE, "ab")  # noqa: SIM115
            self._proc = subprocess.Popen(
                [sys.executable, str(_SERVER_PY)],
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            # Wait up to 10s for server to come up
            for _ in range(20):
                time.sleep(0.5)
                if _check_health():
                    log.info(
                        "[web-server] started (pid=%d, port=%d)", self._proc.pid, _PORT
                    )
                    return
            self._startup_errors.append(
                f"server did not respond within 10s on port {_PORT}"
            )
            log.warning("[web-server] %s", self._startup_errors[-1])
        except Exception as exc:
            self._startup_errors.append(str(exc))
            log.error("[web-server] failed to start: %s", exc)

    def stop(self) -> None:
        """Stop the managed subprocess gracefully."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        _PID_FILE.unlink(missing_ok=True)

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "WebServer",
            "version": "1.0.0",
            "purpose": (
                "Starlette/uvicorn web platform: web UI, WebSocket hub, "
                "agent registration, /health, /metrics, /api/cc_send"
            ),
        }

    def requirements(self) -> dict:
        return {
            "deps": ["starlette", "uvicorn"],
            "system": [f"port {_PORT} available"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": ["ws_broadcast", "agent_register", "cc_send"],
            "mcp_endpoint": None,
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_write",
            "supports_push": True,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        if self._blocked:
            return {
                "status": "unhealthy",
                "detail": f"blocked: {self._block_reason}",
                "checked_at": _now(),
            }
        data = _check_health()
        if data:
            return {
                "status": "healthy",
                "detail": f"port {_PORT} OK, {data.get('agents_attached', 0)} agents",
                "checked_at": _now(),
            }
        return {
            "status": "unhealthy",
            "detail": f"no response on port {_PORT}",
            "checked_at": _now(),
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._startup_errors)

    def logs(self) -> dict:
        return {"paths": {"web_server": str(_LOG_FILE)}}

    def update_info(self) -> dict:
        return {"current_version": "1.0.0", "update_available": False}

    def where_and_how(self) -> dict:
        pid = self._proc.pid if self._proc else None
        if pid is None and _PID_FILE.exists():
            try:
                pid = int(_PID_FILE.read_text().strip())
            except (ValueError, OSError):
                pass
        return {
            "host": "localhost",
            "pid": pid or 0,
            "launch_command": f"{sys.executable} {_SERVER_PY}",
        }

    def restart(self) -> None:
        self._blocked = False
        self._block_reason = ""
        self.stop()
        self.start()

    def block(self, reason: str) -> None:
        self._blocked = True
        self._block_reason = reason

    def halt(self) -> None:
        self.stop()
        self._blocked = True
        self._block_reason = "halt requested"

    def recovery(self) -> None:
        self._blocked = False
        self._block_reason = ""
        if not _check_health():
            self.start()

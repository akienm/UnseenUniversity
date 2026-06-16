"""
plugin_proxy.py — Ground Loop http_proxy mode plugin.

Listens on proxy_port. On each inbound request:
  1. Check if backend is alive on backend_port.
  2. If not: spawn start_cmd (with start_env), wait up to start_timeout seconds.
  3. Forward the request to backend and return the response (HTTP passthrough).
     HTTPS CONNECT tunnel is also supported.

Thread model: one daemon thread runs the HTTP server; proxy / backend
spawn happen in the request handler, protected by a threading.Lock.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError

log = logging.getLogger(__name__)

_IGOR_HOME = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university"))
_FLAGS_DIR = _IGOR_HOME / "flags"
_BACKEND_LOG_DIR = _IGOR_HOME / "ground_loop" / "logs"


class PluginProxy:
    """HTTP proxy that auto-starts the backend on first request after death."""

    def __init__(self, config: dict) -> None:
        self.name: str = config["name"]
        self.proxy_port: int = int(config["proxy_port"])
        self.backend_port: int = int(config["backend_port"])
        self.start_cmd: list[str] = config["start_cmd"]
        self.start_timeout: int = int(config.get("start_timeout", 15))
        self.start_env: dict[str, str] = config.get("start_env", {})
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def breaker_path(self) -> Path:
        return _FLAGS_DIR / f"{self.name}.breaker"

    def _breaker_tripped(self) -> bool:
        return self.breaker_path.exists()

    def _backend_alive(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.backend_port), timeout=1):
                return True
        except (OSError, ConnectionRefusedError):
            return False

    def _spawn_backend(self) -> None:
        env = {**os.environ, **self.start_env}
        # Capture backend stdout+stderr to a logfile rather than DEVNULL.
        # A bare `python3` with a missing import dies on a silent ImportError;
        # DEVNULL made that invisible and turned a one-line log read into a
        # diagnosis session. Append so restarts accumulate, not truncate.
        log_path = _BACKEND_LOG_DIR / f"{self.name}.backend.log"
        try:
            _BACKEND_LOG_DIR.mkdir(parents=True, exist_ok=True)
            backend_log = open(log_path, "ab")
        except OSError as exc:
            log.warning(
                "GROUND_LOOP_PROXY|plugin=%s|warn=backend_log_open_failed|exc=%s",
                self.name, exc,
            )
            backend_log = subprocess.DEVNULL
        log.info(
            "GROUND_LOOP_PROXY|plugin=%s|action=spawn_backend|cmd=%s|backend_port=%d|log=%s",
            self.name, self.start_cmd, self.backend_port, log_path,
        )
        self._proc = subprocess.Popen(
            self.start_cmd,
            env=env,
            stdout=backend_log,
            stderr=subprocess.STDOUT,
        )
        # Popen dups the fd; close our handle so we don't leak one per spawn.
        if backend_log is not subprocess.DEVNULL:
            backend_log.close()
        log.info(
            "GROUND_LOOP_PROXY|plugin=%s|action=spawned|pid=%d",
            self.name, self._proc.pid,
        )

    def _ensure_backend(self) -> bool:
        """Ensure backend is running. Returns True if alive after this call."""
        if self._breaker_tripped():
            log.info("GROUND_LOOP_PROXY|plugin=%s|action=breaker_halt", self.name)
            return False

        if self._backend_alive():
            return True

        with self._lock:
            # Double-check after acquiring lock
            if self._backend_alive():
                return True

            self._spawn_backend()
            # Wait for backend to come up
            deadline = time.monotonic() + self.start_timeout
            while time.monotonic() < deadline:
                if self._backend_alive():
                    log.info(
                        "GROUND_LOOP_PROXY|plugin=%s|event=backend_ready|port=%d",
                        self.name, self.backend_port,
                    )
                    return True
                time.sleep(0.2)

            log.error(
                "GROUND_LOOP_PROXY|plugin=%s|event=backend_start_timeout|timeout=%ds",
                self.name, self.start_timeout,
            )
            return False

    def _make_handler(self) -> type:
        plugin = self

        class ProxyHandler(BaseHTTPRequestHandler):
            server_version = "GroundLoopProxy/1.0"

            def log_message(self, fmt, *args):
                log.debug(
                    "GROUND_LOOP_PROXY|plugin=%s|%s",
                    plugin.name, fmt % args,
                )

            def _forward(self) -> None:
                if not plugin._ensure_backend():
                    self.send_error(503, "Backend unavailable")
                    return

                backend_url = f"http://127.0.0.1:{plugin.backend_port}{self.path}"
                import urllib.request
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length else None

                req = urllib.request.Request(
                    backend_url,
                    data=body,
                    method=self.command,
                    headers={k: v for k, v in self.headers.items()
                              if k.lower() not in ("host", "connection")},
                )
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        self.send_response(resp.status)
                        for key, val in resp.headers.items():
                            if key.lower() not in ("transfer-encoding",):
                                self.send_header(key, val)
                        self.end_headers()
                        self.wfile.write(resp.read())
                except Exception as exc:
                    log.warning(
                        "GROUND_LOOP_PROXY|plugin=%s|error=forward_failed|exc=%s",
                        plugin.name, exc,
                    )
                    self.send_error(502, f"Backend error: {exc}")

            def do_CONNECT(self) -> None:
                # HTTPS CONNECT tunnel
                if not plugin._ensure_backend():
                    self.send_error(503, "Backend unavailable")
                    return
                host, _, port_str = self.path.partition(":")
                port = int(port_str) if port_str else plugin.backend_port
                try:
                    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
                except OSError as exc:
                    self.send_error(502, str(exc))
                    return

                self.send_response(200, "Connection established")
                self.end_headers()

                client = self.connection
                client.setblocking(False)
                sock.setblocking(False)

                import select
                while True:
                    rlist, _, xlist = select.select([client, sock], [], [client, sock], 5)
                    if xlist:
                        break
                    for s in rlist:
                        try:
                            data = s.recv(4096)
                        except (OSError, BlockingIOError):
                            data = b""
                        if not data:
                            sock.close()
                            return
                        other = sock if s is client else client
                        try:
                            other.sendall(data)
                        except OSError:
                            sock.close()
                            return

            do_GET = _forward
            do_POST = _forward
            do_PUT = _forward
            do_DELETE = _forward
            do_PATCH = _forward
            do_HEAD = _forward
            do_OPTIONS = _forward

        return ProxyHandler

    def start(self) -> None:
        handler = self._make_handler()
        self._server = ThreadingHTTPServer(("0.0.0.0", self.proxy_port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"ground_loop_proxy_{self.name}",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "GROUND_LOOP_PROXY|plugin=%s|action=started|proxy_port=%d|backend_port=%d",
            self.name, self.proxy_port, self.backend_port,
        )

    def stop(self) -> None:
        if self._server:
            log.info("GROUND_LOOP_PROXY|plugin=%s|action=stop", self.name)
            self._server.shutdown()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

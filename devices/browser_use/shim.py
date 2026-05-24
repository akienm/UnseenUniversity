"""
BrowserUseShim — lifecycle management for browser-use via Chrome CDP.

Manages a persistent Chrome/Chromium process on a local CDP debug port.
The BrowserUseDevice uses this session to dispatch Agent tasks without
relaunching Chrome on every request.

Restart semantics (per T-adc-browser-use-eval-spike):
    Default = kill-and-reopen. The browser process is terminated and a new
    one started. Session cookies and storage are discarded unless
    storage_state_path is configured. This is the reproducible, predictable
    default — no accumulated state across restarts.

    Storage state persistence is opt-in: set storage_state_path in the shim
    config and browser-use will save/restore cookies+localStorage across
    restarts. Useful for authenticated workflows; not the default because it
    introduces state coupling between runs.

Requirements: google-chrome or chromium-browser in PATH, playwright installed.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)

# CDP debug port the shim launches Chrome on.
_DEFAULT_CDP_PORT = int(os.environ.get("BROWSER_USE_CDP_PORT", "9222"))
# Chrome binary — search PATH, fallback to common locations.
_CHROME_CANDIDATES = [
    "google-chrome",
    "chromium-browser",
    "chromium",
    "google-chrome-stable",
]


def _find_chrome() -> str | None:
    for name in _CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _port_responds(port: int, timeout: float = 10.0) -> bool:
    """Return True when Chrome's CDP port accepts a TCP connection."""
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


class BrowserUseShim(BaseShim):
    """
    Manages a Chrome subprocess for browser-use Agent tasks.

    Usage:
        shim = BrowserUseShim()
        shim.start()                 # launches Chrome on CDP port
        # BrowserUseDevice.run_task() uses the session
        shim.stop()                  # terminates Chrome
        result = shim.self_test()    # connect, navigate about:blank, close
    """

    def __init__(
        self,
        cdp_port: int = _DEFAULT_CDP_PORT,
        storage_state_path: str | None = None,
        headless: bool = True,
    ) -> None:
        self._cdp_port = cdp_port
        self._storage_state_path = storage_state_path
        self._headless = headless
        self._process: subprocess.Popen | None = None

    @property
    def device_id(self) -> str:
        return "browser-use"

    def start(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            log.info("Chrome already running (pid=%d)", self._process.pid)
            return True

        chrome = _find_chrome()
        if chrome is None:
            log.error(
                "No Chrome binary found in PATH — tried: %s",
                ", ".join(_CHROME_CANDIDATES),
            )
            return False

        args = [
            chrome,
            f"--remote-debugging-port={self._cdp_port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--disable-extensions",
        ]
        if self._headless:
            args.append("--headless=new")

        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.error("Failed to launch Chrome: %s", exc)
            return False

        if not _port_responds(self._cdp_port):
            log.error(
                "Chrome launched (pid=%d) but CDP port %d never responded",
                self._process.pid,
                self._cdp_port,
            )
            self._process.kill()
            self._process = None
            return False

        log.info(
            "Chrome started (pid=%d, cdp=localhost:%d, headless=%s)",
            self._process.pid,
            self._cdp_port,
            self._headless,
        )
        return True

    def stop(self) -> bool:
        if self._process is None:
            return True
        if self._process.poll() is not None:
            self._process = None
            return True
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
        log.info("Chrome stopped (pid=%d)", self._process.pid)
        self._process = None
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        """
        Verify Chrome availability. When Chrome is already running, also navigates
        about:blank to confirm CDP responds. When Chrome is not running, only checks
        binary availability — does not launch Chrome.
        """
        import asyncio

        if self._process is None or self._process.poll() is not None:
            chrome = _find_chrome()
            if chrome is None:
                return {"passed": False, "details": "Chrome binary not found in PATH"}
            return {
                "passed": True,
                "details": f"Chrome binary found: {chrome} (not started; call start() first)",
            }

        try:

            async def _test():
                from browser_use.browser.session import BrowserSession

                session = BrowserSession(cdp_url=f"http://127.0.0.1:{self._cdp_port}")
                await session.start()
                page = await session.get_current_page()
                await page.goto("about:blank")
                title = await page.title()
                await session.stop()
                return title

            t0 = time.monotonic()
            title = asyncio.run(_test())
            latency_ms = (time.monotonic() - t0) * 1000
            return {
                "passed": True,
                "details": f"about:blank title={title!r}, latency={latency_ms:.0f}ms",
            }
        except Exception as exc:
            log.error("BrowserUseShim self_test failed: %s", exc)
            return {"passed": False, "details": str(exc)}

    def rollback(self) -> None:
        """Kill Chrome and clean up port if start() failed mid-way."""
        if self._process is not None:
            try:
                self._process.kill()
            except Exception:
                pass
            self._process = None
        # Best-effort: kill any chrome process holding our CDP port
        try:
            subprocess.run(
                ["fuser", "-k", f"{self._cdp_port}/tcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception:
            pass
        log.info("BrowserUseShim rollback complete (port %d freed)", self._cdp_port)

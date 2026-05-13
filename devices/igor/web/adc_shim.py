"""
IgorADCShim — Auto-restart ADC (utility_closet_server) if down on Igor startup.

Igor is a resident of ADC, not its owner. When Igor starts and ADC is not
running, the web UI silently degrades. This shim implements the ADC
specification's auto-restart requirement: any resident can automagically
restart ADC if it's down by reaching for it.

Implements BaseShim from agent_datacenter.shim:
  - start(): ping /health; if down, subprocess-launch ADC and poll for up
  - stop(): send shutdown signal only if Igor owns the process
  - restart(): stop() + start()
  - self_test(): GET /health, return {passed: bool, details: str}
  - rollback(): kill subprocess if start() failed mid-launch

Design rules (palace/rules/coding):
  - OOP-first: class inherits from BaseShim
  - docs-live-in-code: this docstring names the start/stop/restart cycle
  - Fire-and-forget logging: never raise, log failures via self.log
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ADC web port (default 8080, overrideable via IGOR_UC_PORT for testing)
_UC_PORT = int(os.environ.get("IGOR_UC_PORT", "8080"))
# Plain HTTP health port — UC serves HTTPS on _UC_PORT and plain HTTP on _UC_HTTP_PORT.
# Health check must use plain HTTP; HTTPS on 8080 would require cert validation.
_UC_HTTP_PORT = int(os.environ.get("IGOR_UC_HTTP_PORT", "8082"))
_HEALTH_URL = f"http://localhost:{_UC_HTTP_PORT}/health"


def _check_health(timeout_s: float = 3.0) -> bool:
    """Return True if ADC /health responds within timeout_s, False otherwise."""
    try:
        req = urllib.request.Request(_HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, Exception):
        return False


class IgorADCShim:
    """Manages ADC lifecycle: auto-start if down, graceful shutdown."""

    def __init__(self):
        """Initialize the shim. Does not start ADC."""
        self._process: Optional[subprocess.Popen] = None
        self._owns_process = False
        self.log = log

    @property
    def device_id(self) -> str:
        """Unique identifier: the ADC server instance."""
        return "adc-utility-closet"

    def start(self) -> bool:
        """
        Start ADC if not running. Returns True on success, False on timeout.

        Steps:
        1. Ping http://localhost:{_UC_HTTP_PORT}/health (plain HTTP fallback port)
        2. If responds: ADC already up, return True
        3. If no response: subprocess-launch utility_closet_server.py
        4. Poll /health for up to 15s
        5. Return True if healthy, False if timeout

        After failure, rollback() will be called by the caller.
        """
        try:
            # Step 1: Check if already running
            if _check_health(timeout_s=3.0):
                self.log.info("ADC already running at %s", _HEALTH_URL)
                self._owns_process = False
                return True

            # Step 3: Launch ADC subprocess
            self.log.info("ADC not responding — launching utility_closet_server.py")
            try:
                # Find the utility_closet_server.py in the TheIgors venv
                igor_home = Path.home() / "TheIgors"
                server_script = (
                    igor_home / "lab" / "claudecode" / "utility_closet_server.py"
                )

                if not server_script.exists():
                    self.log.error(
                        "utility_closet_server.py not found at %s", server_script
                    )
                    return False

                # Locate the venv Python
                venv_python = igor_home / "venv" / "bin" / "python"
                if not venv_python.exists():
                    self.log.error("TheIgors venv Python not found at %s", venv_python)
                    return False

                # Launch with env vars set
                env = os.environ.copy()
                env["IGOR_UC_PORT"] = str(_UC_PORT)
                self._process = subprocess.Popen(
                    [str(venv_python), str(server_script)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(igor_home),
                    env=env,
                    preexec_fn=None,  # Use default process group
                )
                self._owns_process = True
                self.log.info("Launched ADC subprocess (PID %d)", self._process.pid)
            except Exception as e:
                self.log.error("Failed to launch ADC subprocess: %s", e)
                return False

            # Step 4: Poll /health for up to 15s
            start_time = time.time()
            poll_interval = 0.5  # seconds
            deadline = 15.0  # seconds

            while time.time() - start_time < deadline:
                if _check_health(timeout_s=2.0):
                    self.log.info(
                        "ADC came up after %.1f seconds", time.time() - start_time
                    )
                    return True
                time.sleep(poll_interval)

            # Timeout
            self.log.error(
                "ADC did not respond to /health within %d seconds", int(deadline)
            )
            return False

        except Exception as e:
            self.log.error("IgorADCShim.start() failed: %s", e)
            return False

    def stop(self) -> bool:
        """
        Stop ADC gracefully if Igor owns the process. No-op if already running.

        Returns True on success or if no process to stop, False on error.
        """
        try:
            if not self._owns_process or not self._process:
                self.log.debug("ADC stop: Igor does not own the process, no-op")
                return True

            if self._process.poll() is None:
                # Process still running — send shutdown signal
                self.log.info("Sending SIGTERM to ADC (PID %d)", self._process.pid)
                try:
                    self._process.terminate()
                    # Wait up to 5s for graceful shutdown
                    self._process.wait(timeout=5.0)
                    self.log.info("ADC shut down gracefully")
                except subprocess.TimeoutExpired:
                    self.log.warning(
                        "ADC did not shut down gracefully, killing (SIGKILL)"
                    )
                    self._process.kill()
                    self._process.wait()
                    self.log.info("ADC killed")
            else:
                self.log.debug("ADC process already exited")

            self._process = None
            self._owns_process = False
            return True

        except Exception as e:
            self.log.error("IgorADCShim.stop() failed: %s", e)
            return False

    def restart(self) -> bool:
        """Restart ADC: stop() + start(). Returns True on success."""
        try:
            self.stop()
            return self.start()
        except Exception as e:
            self.log.error("IgorADCShim.restart() failed: %s", e)
            return False

    def self_test(self) -> dict:
        """
        Verify ADC is responding to /health.

        Returns: {passed: bool, details: str}
        """
        try:
            if _check_health(timeout_s=3.0):
                return {"passed": True, "details": f"ADC {_HEALTH_URL} responding"}
            else:
                return {"passed": False, "details": f"ADC {_HEALTH_URL} not responding"}
        except Exception as e:
            return {"passed": False, "details": f"health check error: {e}"}

    def rollback(self) -> None:
        """
        Called when start() returns False. Undo any partial setup.

        Kills the subprocess if start() launched it but failed.
        Safe to call even if start() did nothing.
        """
        try:
            if self._owns_process and self._process and self._process.poll() is None:
                self.log.info(
                    "Rollback: killing ADC subprocess (PID %d)", self._process.pid
                )
                self._process.kill()
                self._process.wait(timeout=5.0)
                self.log.info("Rollback: ADC subprocess killed")
        except subprocess.TimeoutExpired:
            self.log.error("Rollback: ADC subprocess did not die after SIGKILL")
        except Exception as e:
            self.log.error("Rollback failed: %s", e)
        finally:
            self._process = None
            self._owns_process = False

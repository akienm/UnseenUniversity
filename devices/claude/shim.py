"""
ClaudeShim — manages Claude Code session presence on the rack.

start():
  1. ADC health check: ping http://localhost:<ADC_WEB_PORT>/health.
     If no response within 3s, subprocess-launch utility_closet_server.py
     (path configured via ADC_SERVER_PATH env, default ~/TheIgors venv path).
     Poll /health for up to 15s; log result either way.
     ADC failure does NOT block CC startup — this step is advisory.
  2. Create CC.0 mailbox on the IMAP bus.
  3. Register a UserPromptSubmit hook in ~/.claude/settings.json that calls
     ygm_check.py on every query submission.

stop():  Removes the YGM hook from settings.json (leaves CC.0 mailbox
         in place — 24hr retention handles cleanup).

self_test(): Verifies hook is registered, settings.json is valid JSON,
             and ADC /health is reachable.

Ownership rule: ClaudeShim never kills an ADC process it did not launch.

The hook calls:
  python3 -m devices.claude.ygm_check

from the agent_datacenter repo root, so the PYTHONPATH needs to be set
to the repo root in the hook command.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from agent_datacenter.shim import BaseShim
from devices.claude.constants import GLOBAL_MAILBOX, get_session_mailbox

log = logging.getLogger(__name__)

# ── ADC health-check helpers ──────────────────────────────────────────────────
# ADC web port (default 8080, overrideable via ADC_WEB_PORT for testing)
_ADC_PORT = int(os.environ.get("ADC_WEB_PORT", "8080"))
_ADC_HEALTH_URL = f"http://localhost:{_ADC_PORT}/health"

# ADC server script path (configurable for portability; defaults to TheIgors location)
_ADC_SERVER_PATH = os.environ.get(
    "ADC_SERVER_PATH",
    str(Path.home() / "TheIgors" / "lab" / "claudecode" / "utility_closet_server.py"),
)
_ADC_VENV_PYTHON = str(Path.home() / "TheIgors" / "venv" / "bin" / "python")


def _check_adc_health(timeout_s: float = 3.0) -> bool:
    """Return True if ADC /health responds within timeout_s, False otherwise."""
    try:
        req = urllib.request.Request(_ADC_HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, Exception):
        return False


# ── Settings-file helpers ──────────────────────────────────────────────────────

_SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
_HOOK_ID = "ygm-nudge"

# The hook command — uses the repo root PYTHONPATH so devices.claude resolves
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_HOOK_COMMAND = (
    f"cd {_REPO_ROOT} && python3 -m devices.claude.ygm_check 2>/dev/null || true"
)


def _load_settings() -> dict:
    if not os.path.exists(_SETTINGS_PATH):
        return {}
    with open(_SETTINGS_PATH) as f:
        return json.load(f)


def _save_settings(settings: dict) -> None:
    os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
    with open(_SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")


def _hook_entry() -> dict:
    return {
        "id": _HOOK_ID,
        "command": _HOOK_COMMAND,
    }


def _hook_registered(settings: dict) -> bool:
    hooks = settings.get("hooks", {})
    for hook in hooks.get("UserPromptSubmit", []):
        if isinstance(hook, dict) and hook.get("id") == _HOOK_ID:
            return True
    return False


class ClaudeShim(BaseShim):
    """
    Manages Claude's presence on the rack.

    Registers the YGM hook in ~/.claude/settings.json so that every
    query submission triggers a mailbox check and injects an inbox
    summary when mail is waiting.
    """

    def __init__(self, imap_server=None) -> None:
        self._imap = imap_server
        self._adc_process: Optional[subprocess.Popen] = None
        self._adc_owned: bool = False

    @property
    def device_id(self) -> str:
        return "claude"

    def _ensure_adc_running(self) -> bool:
        """
        Ensure ADC (utility_closet_server) is running.

        Returns True if ADC is responding, False on timeout.
        Never raises — all errors are logged and False is returned.
        Caller must not block CC startup on a False return.
        """
        try:
            if _check_adc_health(timeout_s=3.0):
                log.info("ADC already running at %s", _ADC_HEALTH_URL)
                self._adc_owned = False
                return True

            # ADC not responding — try to launch it
            server_path = Path(_ADC_SERVER_PATH)
            venv_python = Path(_ADC_VENV_PYTHON)
            if not server_path.exists():
                log.error("ADC server script not found at %s", server_path)
                return False
            if not venv_python.exists():
                log.error("TheIgors venv Python not found at %s", venv_python)
                return False

            log.info("ADC not responding — launching %s", server_path)
            env = os.environ.copy()
            env["ADC_WEB_PORT"] = str(_ADC_PORT)
            try:
                self._adc_process = subprocess.Popen(
                    [str(venv_python), str(server_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(server_path.parent.parent.parent),
                    env=env,
                )
                self._adc_owned = True
                log.info("Launched ADC subprocess (PID %d)", self._adc_process.pid)
            except Exception as exc:
                log.error("Failed to launch ADC subprocess: %s", exc)
                return False

            # Poll /health for up to 15s
            start_time = time.monotonic()
            deadline = 15.0
            poll_interval = 0.5
            while time.monotonic() - start_time < deadline:
                if _check_adc_health(timeout_s=2.0):
                    elapsed = time.monotonic() - start_time
                    log.info("ADC came up after %.1f seconds", elapsed)
                    return True
                time.sleep(poll_interval)

            log.error("ADC did not respond to /health within %d seconds", int(deadline))
            return False
        except Exception as exc:
            log.error("_ensure_adc_running failed: %s", exc)
            return False

    def start(self) -> bool:
        # Ensure ADC is up before registering hooks.
        # ADC failure must never block CC startup — log and continue.
        if not self._ensure_adc_running():
            log.error(
                "ADC did not come up; proceeding with YGM hook registration anyway"
            )

        # Ensure CC.0 mailbox exists on the bus
        if self._imap is not None:
            try:
                self._imap.create_mailbox(GLOBAL_MAILBOX)
                session_mailbox = get_session_mailbox()
                if session_mailbox != GLOBAL_MAILBOX:
                    self._imap.create_mailbox(session_mailbox)
                log.info(
                    "Claude mailboxes ensured: %s, %s", GLOBAL_MAILBOX, session_mailbox
                )
            except Exception as exc:
                log.warning("Could not ensure Claude mailboxes: %s", exc)

        # Register YGM hook in ~/.claude/settings.json
        try:
            settings = _load_settings()
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Could not read %s: %s", _SETTINGS_PATH, exc)
            return False

        if _hook_registered(settings):
            log.info("YGM hook already registered in %s", _SETTINGS_PATH)
            return True

        hooks = settings.setdefault("hooks", {})
        hooks.setdefault("UserPromptSubmit", []).append(_hook_entry())

        try:
            _save_settings(settings)
            log.info("YGM hook registered in %s", _SETTINGS_PATH)
            return True
        except OSError as exc:
            log.error("Could not write %s: %s", _SETTINGS_PATH, exc)
            return False

    def stop(self) -> bool:
        try:
            settings = _load_settings()
        except (json.JSONDecodeError, OSError):
            return True

        hooks = settings.get("hooks", {})
        before = hooks.get("UserPromptSubmit", [])
        after = [
            h for h in before if not (isinstance(h, dict) and h.get("id") == _HOOK_ID)
        ]
        if len(after) == len(before):
            return True  # already removed

        hooks["UserPromptSubmit"] = after
        if not after:
            del hooks["UserPromptSubmit"]
        if not hooks:
            del settings["hooks"]

        try:
            _save_settings(settings)
            log.info("YGM hook removed from %s", _SETTINGS_PATH)
            return True
        except OSError as exc:
            log.error("Could not write %s: %s", _SETTINGS_PATH, exc)
            return False

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        adc_ok = _check_adc_health(timeout_s=3.0)
        adc_status = (
            f"ADC {_ADC_HEALTH_URL} {'reachable' if adc_ok else 'not reachable'}"
        )

        try:
            settings = _load_settings()
        except json.JSONDecodeError as exc:
            return {
                "passed": False,
                "details": f"{_SETTINGS_PATH} is not valid JSON: {exc}; {adc_status}",
            }
        except FileNotFoundError:
            return {
                "passed": True,
                "details": f"{_SETTINGS_PATH} does not exist yet (start() will create it); {adc_status}",
            }

        if _hook_registered(settings):
            return {
                "passed": True,
                "details": f"YGM hook '{_HOOK_ID}' registered in {_SETTINGS_PATH}; {adc_status}",
            }
        return {
            "passed": False,
            "details": f"YGM hook '{_HOOK_ID}' not found in {_SETTINGS_PATH} — call start(); {adc_status}",
        }

    def rollback(self) -> None:
        self.stop()
        log.info("ClaudeShim rollback complete")

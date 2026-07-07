"""
ClaudeShim — manages Claude Code session presence on the rack.

start():
  1. ADC health check: ping http://localhost:<ADC_WEB_PORT>/health.
     If no response within 3s, start via WebServerDevice.
     ADC failure does NOT block CC startup — this step is advisory.
  2. Create CC.0 mailbox on the bus.
  3. Register a UserPromptSubmit hook in ~/.claude/settings.json that calls
     ygm_check.py on every query submission.

stop():  Removes the YGM hook from settings.json (leaves CC.0 mailbox
         in place — 24hr retention handles cleanup).

self_test(): Verifies hook is registered, settings.json is valid JSON,
             and ADC /health is reachable.

Ownership rule: ClaudeShim never stops an ADC device it did not start.

The hook calls:
  python3 -m unseen_university.devices.claude.ygm_check

from the unseen_university repo root, so the PYTHONPATH needs to be set
to the repo root in the hook command.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

from unseen_university.shim import BaseShim
from unseen_university.devices.claude.constants import GLOBAL_MAILBOX, get_session_mailbox

log = logging.getLogger(__name__)

# ── ADC health-check helpers ──────────────────────────────────────────────────
_ADC_PORT = int(os.environ.get("ADC_WEB_PORT", "8080"))
_ADC_HEALTH_URL = f"http://localhost:{_ADC_PORT}/health"


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
    f"cd {_REPO_ROOT} && python3 -m unseen_university.devices.claude.ygm_check 2>/dev/null || true"
)

# Compaction-cadence Stop hook (D-compact-cadence-hook-2026-06-05): fires
# /autocompact every N ticket-closes. Harness-enforced so the model can't defer.
_STOP_HOOK_ID = "compact-cadence"
_STOP_HOOK_COMMAND = (
    f"cd {_REPO_ROOT} && python3 -m unseen_university.devices.claude.cc_compact_cadence 2>/dev/null || true"
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


def _stop_hook_registered(settings: dict) -> bool:
    """True when the compaction-cadence Stop hook is present.

    Stop entries are nested: each list item is {"hooks": [{...}, ...]}. We tag
    our entry with id=_STOP_HOOK_ID and dedup on it so we never clobber the
    other Stop hooks (cc_log_stop_hook, update-usage) already registered.
    """
    for entry in settings.get("hooks", {}).get("Stop", []):
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []):
            if isinstance(hook, dict) and hook.get("id") == _STOP_HOOK_ID:
                return True
    return False


def _register_stop_hook(settings: dict) -> None:
    """Append the compaction-cadence Stop hook (id-deduped, never clobbers others)."""
    if _stop_hook_registered(settings):
        return
    hooks = settings.setdefault("hooks", {})
    hooks.setdefault("Stop", []).append(
        {"hooks": [{"id": _STOP_HOOK_ID, "type": "command", "command": _STOP_HOOK_COMMAND}]}
    )


def _remove_stop_hook(settings: dict) -> None:
    """Remove only our compaction-cadence Stop entry, leaving other Stop hooks intact."""
    stop = settings.get("hooks", {}).get("Stop", [])
    kept = []
    for entry in stop:
        if isinstance(entry, dict) and any(
            isinstance(h, dict) and h.get("id") == _STOP_HOOK_ID
            for h in entry.get("hooks", [])
        ):
            continue
        kept.append(entry)
    if stop:
        settings["hooks"]["Stop"] = kept
        if not kept:
            del settings["hooks"]["Stop"]


class ClaudeShim(BaseShim):
    """
    Manages Claude's presence on the rack.

    Registers the YGM hook in ~/.claude/settings.json so that every
    query submission triggers a mailbox check and injects an inbox
    summary when mail is waiting.
    """

    def __init__(self, imap_server=None) -> None:
        self._imap = imap_server
        self._adc_owned: bool = False
        self._adc_device = None

    @property
    def device_id(self) -> str:
        return "claude"

    def _ensure_adc_running(self) -> bool:
        """
        Ensure ADC web server is running via WebServerDevice.

        Returns True if ADC is responding, False on failure.
        Never raises — all errors are logged and False is returned.
        Caller must not block CC startup on a False return.
        """
        try:
            if _check_adc_health(timeout_s=3.0):
                log.info("ADC already running at %s", _ADC_HEALTH_URL)
                self._adc_owned = False
                return True

            log.info("ADC not responding — starting via WebServerDevice")
            try:
                from unseen_university.devices.web_server.device import WebServerDevice

                self._adc_device = WebServerDevice()
                self._adc_device.start()
                self._adc_owned = True
            except Exception as exc:
                log.error("Failed to start ADC via WebServerDevice: %s", exc)
                return False

            if _check_adc_health(timeout_s=5.0):
                log.info("ADC web server started successfully")
                return True

            log.error("ADC did not respond to /health after start()")
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

        ygm_already = _hook_registered(settings)
        if not ygm_already:
            hooks = settings.setdefault("hooks", {})
            hooks.setdefault("UserPromptSubmit", []).append(_hook_entry())

        # Compaction-cadence Stop hook — register alongside YGM (idempotent).
        stop_already = _stop_hook_registered(settings)
        if not stop_already:
            _register_stop_hook(settings)

        if ygm_already and stop_already:
            log.info("YGM + compaction-cadence hooks already registered in %s", _SETTINGS_PATH)
            return True

        try:
            _save_settings(settings)
            log.info(
                "Hooks registered in %s (ygm=%s, compact-cadence=%s)",
                _SETTINGS_PATH, not ygm_already, not stop_already,
            )
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
        ygm_changed = len(after) != len(before)
        if ygm_changed:
            hooks["UserPromptSubmit"] = after
            if not after:
                del hooks["UserPromptSubmit"]

        # Remove our compaction-cadence Stop hook (leaves other Stop hooks intact).
        stop_changed = _stop_hook_registered(settings)
        if stop_changed:
            _remove_stop_hook(settings)

        if not ygm_changed and not stop_changed:
            return True  # nothing of ours to remove
        if not settings.get("hooks"):
            settings.pop("hooks", None)

        try:
            _save_settings(settings)
            log.info(
                "Hooks removed from %s (ygm=%s, compact-cadence=%s)",
                _SETTINGS_PATH, ygm_changed, stop_changed,
            )
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

        ygm_ok = _hook_registered(settings)
        stop_ok = _stop_hook_registered(settings)
        if ygm_ok and stop_ok:
            return {
                "passed": True,
                "details": f"YGM '{_HOOK_ID}' + compaction-cadence '{_STOP_HOOK_ID}' registered in {_SETTINGS_PATH}; {adc_status}",
            }
        missing = []
        if not ygm_ok:
            missing.append(f"YGM '{_HOOK_ID}'")
        if not stop_ok:
            missing.append(f"compaction-cadence '{_STOP_HOOK_ID}'")
        return {
            "passed": False,
            "details": f"missing hook(s): {', '.join(missing)} in {_SETTINGS_PATH} — call start(); {adc_status}",
        }

    def rollback(self) -> None:
        self.stop()
        log.info("ClaudeShim rollback complete")

"""
ScrapsDevice — ticket gatekeeper rack device.

Named after one of the Igors' dogs. Validates ticket content before state
transitions: rule-based checks first, optional Qwen 8 fuzzy pass via
InferenceDevice when the rules are ambiguous.

Passing tickets get a `scraps_validated` timestamp stamped into their
metadata. Failing tickets return an issue list so the filer can fix them.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from unseen_university.device import INTERFACE_VERSION, BaseDevice

from unseen_university.devices.scraps import validation_rules

_START_TIME = time.time()
_FUZZY_PROMPT = (
    "You are a ticket quality reviewer. Given the ticket JSON below, "
    "decide whether it contains enough actionable detail for a developer "
    "to start work without asking questions. Reply with exactly one word: "
    "VALID or INVALID, then a colon, then a short reason.\n\nTicket:\n{ticket_json}"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fuzzy_check(ticket: dict) -> tuple[bool, str]:
    """Call InferenceDevice (Qwen 8) for a fuzzy usefulness check.

    Returns (is_valid, reason). Degrades gracefully: if InferenceDevice is
    unavailable or errors, returns (True, "inference unavailable — skipped").
    """
    import json

    try:
        from unseen_university.devices.inference.device import InferenceDevice
        from unseen_university.devices.inference.shim import InferenceRequest

        req = InferenceRequest(
            messages=[
                {
                    "role": "user",
                    "content": _FUZZY_PROMPT.format(
                        ticket_json=json.dumps(ticket, indent=2)
                    ),
                }
            ],
            # Route by domain — VALID/INVALID fuzzy check is a trivial classify task.
            task_class="minion",
            domain="",
            max_tokens=64,
            temperature=0.0,
        )
        resp = InferenceDevice().dispatch(req)
        text = (resp.text or "").strip()
        if text.upper().startswith("VALID"):
            return True, text
        if text.upper().startswith("INVALID"):
            return False, text
        # Unrecognised format — don't block
        return True, f"unrecognised response: {text!r}"
    except Exception as exc:
        return True, f"inference unavailable — skipped ({exc})"


class ScrapsDevice(BaseDevice):
    """
    In-process ticket gatekeeper. No subprocess, no DB.

    validate_ticket() is the primary API; the rack MCP server surfaces it
    as scraps_validate_ticket via the librarian tools layer.
    """

    DEVICE_ID = "scraps"

    def __init__(self) -> None:
        super().__init__()
        self._startup_errors: list[str] = []
        self._blocked: bool = False
        self._block_reason: str = ""
        # Wire the shim (aider pattern) — the shim owns Scraps's in-process job-runner
        # loop. The retired scraps.yaml PluginDaemon subprocess no longer runs it
        # (T-collapse-daemons-to-ground-loop).
        from unseen_university.devices.scraps.shim import ScrapsShim

        self._shim = ScrapsShim()

    # ── Primary API ──────────────────────────────────────────────────────────

    def embed_text(self, text: str, model: str = "auto") -> dict[str, Any]:
        """Compute a text embedding. Returns {vector, model, dimension}.

        model='auto' selects OpenAI text-embedding-3-small when OPENAI_API_KEY
        is set, falling back to hash-sha256-384 otherwise. 'auto' is the only
        supported value; other values are silently treated as 'auto'.

        Caller owns any DB write — this method only computes.
        """
        from unseen_university.devices.scraps.embedding_engine import embed as _embed

        return _embed(text)

    def validate_ticket(self, ticket: dict, *, silent: bool = False) -> dict[str, Any]:
        """Validate ticket content; return {valid, issues, validated_at}.

        Pass 1: rule-based checks (always run).
        Pass 2: Qwen 8 fuzzy check (only when rules pass and description
                is short/ambiguous — keeps inference budget low).

        On pass: validated_at is an ISO-8601 timestamp.
        On fail: validated_at is None; issues lists what to fix.

        silent=True suppresses all channel posts — use for self-test / diagnostic callers.
        """
        tid = ticket.get("id") or ticket.get("title", "?")[:40]
        issues = validation_rules.run_all(ticket)

        if not issues:
            desc_len = len((ticket.get("description") or "").strip())
            if desc_len < 80:
                if not silent:
                    self._post(
                        "shared",
                        f"Scraps: {tid} — rules passed, desc short ({desc_len}c), running fuzzy check",
                    )
                ok, reason = _fuzzy_check(ticket)
                if not ok:
                    issues.append(f"fuzzy check: {reason}")
                    if not silent:
                        self._post(
                            "shared",
                            f"Scraps: {tid} — fuzzy check INVALID: {reason[:120]}",
                        )

        if issues:
            issues_str = "; ".join(issues)
            if not silent:
                self._post("shared", f"Scraps: {tid} — validation failed: {issues_str}")
            return {"valid": False, "issues": issues, "validated_at": None}

        return {"valid": True, "issues": [], "validated_at": _now()}

    # ── BaseDevice contract ──────────────────────────────────────────────────

    AGENT_CLASS = "utility"

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "ScrapsDevice",
            "version": "0.1.0",
            "purpose": (
                "Ticket gatekeeper: validates content before state transitions. "
                "Rule-based V1 with optional Qwen 8 fuzzy pass."
            ),
            "agent_class": self.AGENT_CLASS,
        }

    def requirements(self) -> dict:
        return {"deps": []}

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": False,
            "emitted_keywords": ["scraps_validated"],
            "mcp_endpoints": ["scraps_validate_ticket", "scraps_embed_text"],
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/validate",
            "mode": "read_only",
            "supports_push": False,
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
        if self._startup_errors:
            return {
                "status": "degraded",
                "detail": "; ".join(self._startup_errors),
                "checked_at": _now(),
            }
        return {"status": "healthy", "detail": "rule engine ok", "checked_at": _now()}

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._startup_errors)

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.environ.get("HOSTNAME", "localhost"),
            "pid": os.getpid(),
            "launch_command": "in-process — no subprocess",
        }

    def restart(self) -> None:
        pass

    def block(self, reason: str) -> None:
        self._blocked = True
        self._block_reason = reason

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._blocked = False
        self._block_reason = ""
        self._startup_errors.clear()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _post(self, channel: str, message: str) -> None:
        try:
            from unseen_university.channel import post_to_channel

            post_to_channel(message, author="scraps", channel=channel)
        except Exception:
            pass

"""
VetinariDevice — meta-orchestrator for the agent collective.

Lord Vetinari manages the whole rack without anyone noticing. He knows what
every factory and agent is doing, holds owner_id for factories without a more
specific owner, makes high-level resource allocation decisions, and reports to
Akien when human decisions are required.

PA2.0 Layer 3 (C-prescient-agents-pa20, G-factory-of-factories):
  factory lifecycle management → agent health rollup → budget reallocation
  → cross-factory goal tracking → Akien escalation when needed.

Design rules: BaseDevice/BaseShim; Vetinari calls tools, does not contain
systems. External state for factory registry (flat-file JSON) so it restarts
freely (see feedback_external_state_principle).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from unseen_university.device import BaseDevice, INTERFACE_VERSION

log = logging.getLogger(__name__)

_DEFAULT_ESCALATION_THRESHOLD = 0.5
_VETINARI_VERSION = "0.1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _factory_registry_path() -> Path:
    root = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university"))
    return root / "vetinari" / "factories.json"


def _pending_directives_path() -> Path:
    root = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university"))
    return root / "vetinari" / "pending_directives.json"


class VetinariDevice(BaseDevice):
    """Meta-orchestrator device.

    Owns factory specs, aggregates health rollups across the collective, and
    escalates to Akien when eval scores drop below threshold.
    """

    DEVICE_ID = "vetinari"

    def __init__(
        self,
        escalation_threshold: float = _DEFAULT_ESCALATION_THRESHOLD,
        channel_post_fn=None,
    ) -> None:
        super().__init__()
        self._start_time = time.time()
        self._blocked = False
        self._block_reason = ""
        self._startup_errors: list[str] = []
        self._escalation_threshold = escalation_threshold
        # Injected in production; default reads from unseen_university.channel
        self._channel_post = channel_post_fn or self._default_channel_post
        self._load_factories()

    # ── Factory registry (external state — flat file) ─────────────────────────

    def _load_factories(self) -> None:
        path = _factory_registry_path()
        if path.exists():
            try:
                self._factories: dict[str, dict] = json.loads(path.read_text())
            except Exception as exc:
                log.warning("VetinariDevice: factory registry load error: %s", exc)
                self._startup_errors.append(f"factory registry load: {exc}")
                self._factories = {}
        else:
            self._factories = {}

    def _save_factories(self) -> None:
        path = _factory_registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._factories, indent=2))
        log.info("VetinariDevice: factory registry saved (%d factories)", len(self._factories))

    # ── Public API ────────────────────────────────────────────────────────────

    def own_factory(self, factory_id: str, spec: dict) -> None:
        """Register a factory spec under Vetinari's ownership.

        Vetinari becomes the owner_id for this factory. He will monitor its
        health and escalate to Akien when needed.
        """
        self._factories[factory_id] = {
            "factory_id": factory_id,
            "spec": spec,
            "owner_id": "comms://vetinari/",
            "registered_at": _now(),
            "last_health": None,
            "last_eval_score": None,
        }
        self._save_factories()
        log.info("VetinariDevice: owned factory %s", factory_id)

    def receive_health_rollup(self, factory_id: str, health: dict) -> bool:
        """Receive a health update for a factory. Returns True if escalated.

        health dict shape (flexible):
          eval_score: float 0.0–1.0 — composite quality score
          status: str — "healthy" | "degraded" | "unhealthy"
          detail: str — optional human-readable detail
        """
        if factory_id not in self._factories:
            log.warning("VetinariDevice: health rollup for unknown factory %s", factory_id)
            return False

        self._factories[factory_id]["last_health"] = health
        self._factories[factory_id]["last_health_at"] = _now()

        eval_score = health.get("eval_score")
        if eval_score is not None:
            self._factories[factory_id]["last_eval_score"] = eval_score

        self._save_factories()
        log.info(
            "VetinariDevice: health rollup %s — score=%s status=%s",
            factory_id,
            eval_score,
            health.get("status"),
        )

        if eval_score is not None and eval_score < self._escalation_threshold:
            self._escalate_to_akien(factory_id, eval_score, health)
            return True
        return False

    def halt_factory(self, factory_id: str, reason: str = "") -> None:
        """Mark a factory as halted in the registry."""
        if factory_id in self._factories:
            self._factories[factory_id]["status"] = "halted"
            self._factories[factory_id]["halted_at"] = _now()
            self._factories[factory_id]["halt_reason"] = reason
            self._save_factories()
            log.info("VetinariDevice: halted factory %s — %s", factory_id, reason)

    def get_owned_factories(self) -> list[dict]:
        """Return all factories owned by Vetinari."""
        return list(self._factories.values())

    # ── Directive intake (T-vetinari-directive-intake) ────────────────────────

    def accept_directive(self, directive: dict) -> bool:
        """Append a directive to pending_directives.json. Idempotent by id field.

        Returns True when added, False when a duplicate id was detected.
        Atomic write: write to .tmp then rename, so a crash mid-write
        leaves the file intact.
        """
        path = _pending_directives_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        directives: list[dict] = []
        if path.exists():
            try:
                directives = json.loads(path.read_text())
            except Exception as exc:
                log.warning("VetinariDevice: pending_directives load error: %s", exc)
                directives = []

        directive_id = directive.get("id", "")
        if directive_id and any(d.get("id") == directive_id for d in directives):
            log.info("VetinariDevice: duplicate directive %r — skipping", directive_id)
            return False

        directives.append(directive)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(directives, indent=2))
        tmp.rename(path)
        log.info(
            "VetinariDevice: accepted directive %r (%d pending)",
            directive_id,
            len(directives),
        )
        return True

    def get_pending_directives(self) -> list[dict]:
        """Return all pending directives from flat file."""
        path = _pending_directives_path()
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text())
        except Exception as exc:
            log.warning("VetinariDevice: pending_directives read error: %s", exc)
            return []

    # ── Internal ──────────────────────────────────────────────────────────────

    def _escalate_to_akien(
        self, factory_id: str, eval_score: float, health: dict
    ) -> None:
        msg = (
            f"VETINARI_ESCALATE factory={factory_id} "
            f"eval_score={eval_score:.3f} "
            f"threshold={self._escalation_threshold} "
            f"status={health.get('status','?')} "
            f"detail={health.get('detail','')!r}"
        )
        self._channel_post(msg)
        log.info(
            "VetinariDevice: escalated factory %s to Akien — eval_score=%.3f < threshold=%.3f",
            factory_id,
            eval_score,
            self._escalation_threshold,
        )

    @staticmethod
    def _default_channel_post(message: str) -> None:
        try:
            from unseen_university.channel import post_to_channel
            post_to_channel(message)
        except Exception as exc:
            log.warning("VetinariDevice: channel post failed: %s", exc)

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Vetinari",
            "version": _VETINARI_VERSION,
            "purpose": "Meta-orchestrator — factory lifecycle, health rollup, Akien escalation",
            "owned_factories": len(self._factories),
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def requirements(self) -> dict:
        return {"deps": ["channel"]}

    def capabilities(self) -> dict:
        return {
            "can_send": True,
            "can_receive": True,
            "emitted_keywords": ["VETINARI_ESCALATE"],
        }

    def comms(self) -> dict:
        return {
            "address": "comms://vetinari/",
            "mode": "push",
            "push": True,
            "pull": False,
            "nudge": False,
        }

    def where_and_how(self) -> dict:
        import socket
        return {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "launch_command": "devices/vetinari/device.py",
        }

    def health(self) -> dict:
        degraded = [
            fid for fid, f in self._factories.items()
            if (f.get("last_eval_score") or 1.0) < self._escalation_threshold
        ]
        status = "healthy" if not degraded else "degraded"
        return {
            "status": status,
            "detail": f"{len(self._factories)} factories owned; {len(degraded)} below threshold",
            "checked_at": _now(),
            "owned_factory_count": len(self._factories),
            "degraded_factories": degraded,
        }

    def uptime(self) -> float:
        return time.time() - self._start_time

    def startup_errors(self) -> list:
        return list(self._startup_errors)

    def logs(self) -> dict:
        root = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university"))
        return {
            "vetinari": str(root / "logs" / "vetinari" / "vetinari.log"),
        }

    def update_info(self) -> dict:
        return {"current_version": _VETINARI_VERSION, "update_available": False}

    def restart(self) -> None:
        log.info("VetinariDevice: restart — reloading factory registry")
        self._load_factories()

    def block(self, reason: str) -> None:
        self._blocked = True
        self._block_reason = reason
        log.info("VetinariDevice: blocked — %s", reason)

    def halt(self) -> None:
        log.info("VetinariDevice: halt")
        self._blocked = True
        self._block_reason = "halted"

    def recovery(self) -> None:
        self._blocked = False
        self._block_reason = ""
        self._load_factories()
        log.info("VetinariDevice: recovery — unblocked, factory registry reloaded")

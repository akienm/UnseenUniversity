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
from unseen_university._uu_root import uu_home

import json
import logging
import os
import subprocess
import time
import urllib.request
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
    root = Path(uu_home())
    return root / "vetinari" / "factories.json"


def _pending_directives_path() -> Path:
    root = Path(uu_home())
    return root / "vetinari" / "pending_directives.json"


def _audit_log_path() -> Path:
    root = Path(uu_home())
    return root / "vetinari" / "audit.jsonl"


# ── Team routing (T-vetinari-team-dispatch) ───────────────────────────────────

ROUTING_TABLE: dict[str, str] = {
    # Build / code / infrastructure → CC
    "build": "claude",
    "code": "claude",
    "architecture": "claude",
    "platform": "claude",
    "infrastructure": "claude",
    "security": "claude",
    "database": "claude",
    "cognition": "claude",
    # Research / memory / reading → Librarian
    "research": "librarian",
    "memory": "librarian",
    "reading": "librarian",
    "librarian": "librarian",
    "watchlist": "librarian",
    # Review / check / summarize → DickSimnel (token-light)
    "review": "dicksimnel",
    "check": "dicksimnel",
    "summarize": "dicksimnel",
    "summary": "dicksimnel",
    "audit": "dicksimnel",
}


def _route_worker(tags: list[str]) -> str:
    """Return the canonical worker name for a list of ticket tags.

    Checks ROUTING_TABLE (case-insensitive) and returns the first match.
    Defaults to 'claude' when no tag matches.
    """
    for tag in tags:
        worker = ROUTING_TABLE.get(tag.lower())
        if worker:
            return worker
    return "claude"


CLARIFY_THRESHOLD = float(os.environ.get("VETINARI_CLARIFY_THRESHOLD", "0.7"))

_OR_BASE = "https://openrouter.ai/api/v1"
_OR_REFERER = "https://github.com/akienm/TheIgors"
_OR_MODEL = os.environ.get("VETINARI_LLM_MODEL", "openai/gpt-4o-mini")
_DECOMPOSE_PROMPT_PATH = Path(__file__).parent / "decompose_prompt.txt"
_CC_QUEUE = Path(__file__).resolve().parents[2] / "devlab" / "claudecode" / "cc_queue.py"


def _call_llm_or(directive_text: str) -> str:
    """Call OpenRouter to decompose a directive. Returns raw response text."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set — cannot call LLM for decompose")

    system = (
        _DECOMPOSE_PROMPT_PATH.read_text()
        if _DECOMPOSE_PROMPT_PATH.exists()
        else "Decompose the directive into JSON work tickets."
    )
    payload = json.dumps(
        {
            "model": _OR_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": directive_text},
            ],
            "temperature": 0.2,
            "max_tokens": 2000,
        }
    ).encode()
    req = urllib.request.Request(
        f"{_OR_BASE}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _OR_REFERER,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def _parse_subtasks(raw: str) -> list[dict]:
    """Parse LLM response into a list of subtask dicts.

    Strips markdown fences before parsing. Raises ValueError on invalid JSON
    or if the result is not a list.
    """
    text = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            l for l in lines if not l.startswith("```")
        ).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM response is not valid JSON: {exc}\nRaw: {raw[:300]}") from exc
    if not isinstance(result, list):
        raise ValueError(f"LLM response is not a JSON array: {type(result)}")
    if not result:
        raise ValueError("LLM returned empty subtask list")
    return result


def _parse_decompose_response(raw: str) -> tuple:
    """Parse LLM response; return (confidence, subtasks, clarification_question).

    Handles both formats for backward compatibility:
    - JSON array  → confidence=1.0, no question (old format, no retry needed)
    - JSON object → {confidence, subtasks, clarification_question}

    Raises ValueError on unparseable or structurally wrong responses.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines() if not l.startswith("```")).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM response not valid JSON: {exc}\nRaw: {raw[:200]}") from exc

    if isinstance(result, list):
        if not result:
            raise ValueError("LLM returned empty subtask list")
        return (1.0, result, "")

    if isinstance(result, dict):
        confidence = float(result.get("confidence", 1.0))
        subtasks = result.get("subtasks") or []
        question = result.get("clarification_question", "")
        if not subtasks and confidence >= CLARIFY_THRESHOLD:
            raise ValueError("LLM returned no subtasks at high confidence")
        return (confidence, subtasks, question)

    raise ValueError(f"LLM response must be array or object, got {type(result).__name__}")


def _write_tickets_to_queue(subtasks: list[dict], decision_id: str = "") -> list[str]:
    """Write subtasks to cc_queue via subprocess. Returns list of ticket IDs.

    Generates ticket IDs from slugified titles. Writes a temp JSON batch
    file and calls cc_queue.py add. Idempotent per ID.
    """
    import re
    import sys
    import tempfile

    tickets = []
    for i, sub in enumerate(subtasks):
        title = (sub.get("title") or f"untitled-{i}")[:80]
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:40].strip("-")
        ticket_id = f"T-vetinari-{slug}"
        tags = sub.get("tags") or ["Vetinari"]
        worker = _route_worker(tags)
        ticket = {
            "id": ticket_id,
            "title": title,
            "description": sub.get("description", "Vetinari-decomposed subtask."),
            "worker": worker,
            "tags": tags,
            "size": sub.get("size", "M"),
            "status": "sprint",
            "decision_id": decision_id,
            "priority": 0.5,
        }
        tickets.append(ticket)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="vetinari_batch_", delete=False
    ) as f:
        json.dump(tickets, f)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, str(_CC_QUEUE), "add", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"cc_queue.py add failed: {result.stderr.strip()}"
            )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return [t["id"] for t in tickets]


def _query_ticket_status(ticket_id: str) -> str | None:
    """Return ticket status from cc_queue.py show, or None on error/missing."""
    import sys
    try:
        result = subprocess.run(
            [sys.executable, str(_CC_QUEUE), "show", ticket_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("status")
    except Exception as exc:
        log.warning("_query_ticket_status %r: %s", ticket_id, exc)
        return None


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

    # ── Directive decomposition (T-vetinari-decompose) ───────────────────────

    def decompose_directive(
        self,
        directive_id: str,
        llm_fn=None,
    ) -> list[str]:
        """Decompose a pending directive into cc_queue tickets.

        Loads the directive from pending_directives.json, calls llm_fn
        (defaults to _call_llm_or) to produce a list of subtask dicts,
        writes each to cc_queue via cc_queue.py add, stores child_ticket_ids,
        and transitions the directive status from pending → active.

        Returns list of ticket IDs created.
        Raises ValueError when directive_id is not found.
        LLM parse errors: retry once, then raise.
        """
        # Load directive
        directives = self.get_pending_directives()
        directive = next((d for d in directives if d.get("id") == directive_id), None)
        if directive is None:
            raise ValueError(f"directive {directive_id!r} not found in pending_directives")

        text = directive.get("text", "")
        call = llm_fn or _call_llm_or

        # Attempt decompose — retry once on parse failure
        confidence: float = 1.0
        subtasks: list[dict] = []
        question: str = ""
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                raw = call(text)
                confidence, subtasks, question = _parse_decompose_response(raw)
                break
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "VetinariDevice: decompose attempt %d failed: %s",
                    attempt + 1,
                    exc,
                )
        else:
            raise ValueError(
                f"decompose_directive: LLM failed after 2 attempts: {last_exc}"
            )

        # CP1: if confidence below threshold, ask Akien before proceeding
        if confidence < CLARIFY_THRESHOLD:
            clarify_q = question or f"Please clarify this directive: {text[:120]!r}"
            self._post_clarification_question(directive_id, clarify_q)
            for d in directives:
                if d.get("id") == directive_id:
                    d["status"] = "awaiting_clarification"
                    d["clarification_question"] = clarify_q
            path = _pending_directives_path()
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(directives, indent=2))
            tmp.rename(path)
            self._audit_log(
                event="CLARIFY",
                reason=f"confidence={confidence:.2f} < threshold={CLARIFY_THRESHOLD}",
                context={"question": clarify_q},
                directive_id=directive_id,
            )
            log.info(
                "VetinariDevice: CP1 — directive %r needs clarification (confidence=%.2f)",
                directive_id,
                confidence,
            )
            return []

        # Write tickets to cc_queue and collect IDs
        child_ids = _write_tickets_to_queue(subtasks, decision_id=directive_id)

        # Update directive state → active
        for d in directives:
            if d.get("id") == directive_id:
                d["status"] = "active"
                d["child_ticket_ids"] = child_ids
                d["decomposed_at"] = _now()
        path = _pending_directives_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(directives, indent=2))
        tmp.rename(path)
        self._audit_log(
            event="DECOMPOSE",
            reason=f"decomposed directive into {len(child_ids)} tickets",
            context={"child_ticket_ids": child_ids, "directive_text_preview": text[:100]},
            directive_id=directive_id,
        )
        log.info(
            "VetinariDevice: directive %r decomposed → %d tickets: %s",
            directive_id,
            len(child_ids),
            child_ids,
        )
        return child_ids

    # ── Directive progress tracking (T-vetinari-progress-tracking) ──────────

    def check_directive_progress(self, directive_id: str) -> dict:
        """Poll cc_queue for each child ticket; update directive state.

        Returns a progress snapshot: {open, in_progress, closed, missing}.
        Updates the directive record with latest snapshot and status.
        'completed' only when all children are closed.
        Gracefully handles missing tickets (counted as 'missing', not fatal).
        """
        directives = self.get_pending_directives()
        directive = next((d for d in directives if d.get("id") == directive_id), None)
        if directive is None:
            return {"open": 0, "in_progress": 0, "closed": 0, "missing": 0}

        child_ids = directive.get("child_ticket_ids", [])
        counts: dict[str, int] = {"open": 0, "in_progress": 0, "closed": 0, "missing": 0}

        for ticket_id in child_ids:
            status = _query_ticket_status(ticket_id)
            if status is None:
                counts["missing"] += 1
            elif status in ("done", "closed", "cancelled"):
                counts["closed"] += 1
            elif status == "in_progress":
                counts["in_progress"] += 1
            else:
                counts["open"] += 1

        # Determine directive status
        total = len(child_ids)
        if total == 0:
            new_status = directive.get("status", "active")
        elif counts["closed"] + counts["missing"] == total:
            new_status = "completed"
        elif counts["open"] + counts["in_progress"] > 0:
            new_status = "active"
        else:
            new_status = directive.get("status", "active")

        # Update directive flat-file state + fire completion signal (once)
        fire_completion = False
        for d in directives:
            if d.get("id") == directive_id:
                d["progress"] = counts
                d["status"] = new_status
                if new_status == "completed" and not d.get("completed_at"):
                    d["completed_at"] = _now()
                    fire_completion = True

        path = _pending_directives_path()
        if path.exists():
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(directives, indent=2))
            tmp.rename(path)
        log.info(
            "VetinariDevice: progress %r → %s (open=%d in_progress=%d closed=%d missing=%d)",
            directive_id,
            new_status,
            counts["open"],
            counts["in_progress"],
            counts["closed"],
            counts["missing"],
        )
        if fire_completion:
            self._notify_directive_complete(directive_id, child_count=len(child_ids))
        return counts

    def get_directive_status(self, directive_id: str) -> str:
        """Return current status for directive: 'active'|'completed'|'awaiting_clarification'|'unknown'."""
        directives = self.get_pending_directives()
        directive = next((d for d in directives if d.get("id") == directive_id), None)
        if directive is None:
            return "unknown"
        return directive.get("status", "active")

    # ── CP1 clarification loop (T-vetinari-clarification-loop) ──────────────

    def _post_clarification_question(self, directive_id: str, question: str) -> None:
        """Post a clarification request to the channel (CP1: ask rather than guess)."""
        msg = (
            f"VETINARI_CLARIFY directive={directive_id} "
            f"question={question!r}"
        )
        self._channel_post(msg)
        log.info(
            "VetinariDevice: CP1 clarification posted for directive %r: %s",
            directive_id,
            question[:80],
        )

    def handle_clarification_reply(
        self,
        directive_id: str,
        reply_text: str,
        llm_fn=None,
    ) -> list[str]:
        """Process Akien's clarification reply: enrich directive text and re-decompose.

        Appends the clarification context to the directive's text field, resets
        status to allow re-decomposition, then calls decompose_directive() again.
        Returns the list of ticket IDs created (may be empty if still ambiguous).
        """
        directives = self.get_pending_directives()
        directive = next((d for d in directives if d.get("id") == directive_id), None)
        if directive is None:
            raise ValueError(f"directive {directive_id!r} not found")

        enriched = directive.get("text", "") + f"\n\nClarification from Akien: {reply_text}"

        for d in directives:
            if d.get("id") == directive_id:
                d["text"] = enriched
                d["status"] = "pending"

        path = _pending_directives_path()
        if path.exists():
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(directives, indent=2))
            tmp.rename(path)

        self._audit_log(
            event="CLARIFY_REPLY",
            reason="Akien provided clarification; re-attempting decompose",
            context={"reply_preview": reply_text[:100]},
            directive_id=directive_id,
        )
        log.info(
            "VetinariDevice: clarification reply received for %r — re-decomposing",
            directive_id,
        )
        return self.decompose_directive(directive_id, llm_fn=llm_fn)

    # ── CP3/CP6 audit log (T-vetinari-cp-audit) ──────────────────────────────

    def _audit_log(
        self,
        event: str,
        reason: str,
        context: dict,
        directive_id: str = "",
    ) -> None:
        """Append a structured audit entry to audit.jsonl (CP3/CP6 compliance).

        Format: one JSON object per line — {ts, directive_id, event, reason, context}.
        Append-only; never deletes. Every dispatch and escalation decision is logged
        with a 'reason' so the reasoning is auditable (CP3) and escalations are
        traceable (CP6).
        """
        path = _audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _now(),
            "directive_id": directive_id,
            "event": event,
            "reason": reason,
            "context": context,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        log.debug("VetinariDevice: audit %s directive=%r reason=%r", event, directive_id, reason[:80])

    def get_audit_log(self, directive_id: str | None = None) -> list[dict]:
        """Return audit log entries, optionally filtered by directive_id."""
        path = _audit_log_path()
        if not path.exists():
            return []
        entries = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if directive_id is None or entry.get("directive_id") == directive_id:
                        entries.append(entry)
                except json.JSONDecodeError:
                    log.warning("VetinariDevice: malformed audit entry skipped")
        except Exception as exc:
            log.warning("VetinariDevice: audit log read error: %s", exc)
        return entries

    def _notify_directive_complete(self, directive_id: str, child_count: int = 0) -> None:
        """Post VETINARI_COMPLETE to channel when a directive finishes.

        Idempotency is enforced by the caller (check_directive_progress guards
        on completed_at not yet set). Only fires once per directive lifetime.
        """
        msg = (
            f"VETINARI_COMPLETE directive={directive_id} "
            f"tickets={child_count}"
        )
        self._channel_post(msg)
        self._audit_log(
            event="COMPLETE",
            reason=f"all {child_count} child tickets closed",
            context={"child_count": child_count},
            directive_id=directive_id,
        )
        log.info("VetinariDevice: VETINARI_COMPLETE posted for directive %r", directive_id)

    # ── System alarm escalation (T-vetinari-owns-alarm-escalation) ──────────────

    def sweep_system_alarms(self, *, now=None) -> int:
        """Escalate new/reopened system alarms to the channel.

        Calls notify_new_alarms with self._escalate_alarm as the send_fn.
        Returns the number of alarms escalated.
        Reuses the existing dedup and reopened-re-post logic from notify_new_alarms.
        """
        from unseen_university.system_alarm_notifier import notify_new_alarms

        return notify_new_alarms(send_fn=self._escalate_alarm, now=now)

    def _escalate_alarm(self, summary: str) -> bool:
        """Post a system alarm summary to the channel.

        Called by notify_new_alarms for each new/reopened alarm.
        Posts to the channel, audit-logs the escalation, and logs at INFO.
        Returns True on successful post, False on failure (so notify_new_alarms
        only stamps mark_notified when the post actually succeeded).
        """
        try:
            self._channel_post(summary)
            self._audit_log(
                event="ALARM_ESCALATE",
                reason="new or reopened system alarm",
                context={"summary": summary},
            )
            log.info("VetinariDevice: escalated system alarm — %s", summary)
            return True
        except Exception as exc:
            log.warning("VetinariDevice: alarm escalation failed: %s", exc)
            return False

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
        self._audit_log(
            event="ESCALATE",
            reason=f"eval_score={eval_score:.3f} < threshold={self._escalation_threshold}",
            context={"factory_id": factory_id, "health": health},
        )
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
        root = Path(uu_home())
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

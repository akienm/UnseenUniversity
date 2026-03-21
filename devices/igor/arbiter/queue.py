"""
Arbiter queue — pending human-approval items (change.33).

File-backed JSON at:
  ~/.TheIgors/igor_wild_0001/arbiter/pending.json

Igor submits uncertain actions here before executing them.
Akien reviews via /arbiter commands or the web dashboard.
Approved/denied decisions feed back as EPISODIC memories so
Igor learns akien's threshold over time.

Tool: arbiter_submit — Igor can queue an action during reasoning.

Threshold criteria (any one triggers queuing):
  - Irreversible actions (delete, send, publish, purchase, email…)
  - Actions affecting external systems Igor does not own
  - Actions flagged by validate_against_core as ethics concerns
  - Cost above configurable limit (default $1.00 per action)
  - Manuel submission via arbiter_submit tool
"""

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..tools.registry import Tool, registry
from ..paths import paths

ARBITER_DIR = paths().arbiter_dir
PENDING_PATH = ARBITER_DIR / "pending.json"

# Keywords that indicate an NE action impulse may be irreversible/external
IRREVERSIBLE_KEYWORDS = {
    "send",
    "delete",
    "publish",
    "purchase",
    "email",
    "message",
    "remove",
    "modify",
    "post",
    "notify",
    "alert",
    "deploy",
    "write_file",
    "execute",
    "broadcast",
}

_lock = threading.Lock()


@dataclass
class ArbiterItem:
    id: int
    timestamp: str
    action_type: (
        str  # irreversible | external_system | high_cost | ethics_flag | manual
    )
    description: str  # What Igor was about to do
    context: str  # Why Igor thinks this is needed
    threshold_reason: str  # Which criterion triggered the flag
    status: str = "pending"  # pending | approved | denied
    cost_estimate: float = 0.0
    metadata: dict = field(default_factory=dict)
    resolution_ts: str = ""
    resolution_note: str = ""


# ── File I/O ──────────────────────────────────────────────────────────────────


def _load() -> list[dict]:
    if not PENDING_PATH.exists():
        return []
    try:
        return json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(items: list[dict]):
    ARBITER_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")


# ── Public API ────────────────────────────────────────────────────────────────


def submit(
    description: str,
    context: str = "",
    action_type: str = "manual",
    threshold_reason: str = "",
    cost_estimate: float = 0.0,
    metadata: dict = None,
) -> int:
    """
    Submit an action for human approval. Returns new item ID. Thread-safe.
    Sends a one-per-item Discord ping if DISCORD_CHANNEL_ID is configured.
    Returns 0 without queuing when IGOR_ARBITER_ENABLED=false.
    """
    if os.getenv("IGOR_ARBITER_ENABLED", "true").lower() in ("false", "0", "no"):
        return 0
    with _lock:
        items = _load()
        new_id = max((i["id"] for i in items), default=0) + 1
        item = ArbiterItem(
            id=new_id,
            timestamp=datetime.now().isoformat(),
            action_type=action_type,
            description=description,
            context=context,
            threshold_reason=threshold_reason,
            cost_estimate=cost_estimate,
            metadata=metadata or {},
        )
        items.append(asdict(item))
        _save(items)

    _ping_discord(new_id, description)
    # #105: alert CC if queue is building up
    pending_count = count_pending()
    if pending_count > 3:
        try:
            from ..cognition.forensic_logger import log_anomaly as _la

            _la(
                kind="ARBITER_BUILDUP",
                detail=f"pending={pending_count}|newest={description[:80]}",
            )
        except Exception as _bare_e:
            log_error(kind="BARE_EXCEPT", detail=f"wild_igor/igor/arbiter/queue.py: {_bare_e}")
    return new_id


def get_pending() -> list[ArbiterItem]:
    return [ArbiterItem(**i) for i in _load() if i.get("status") == "pending"]


def get_item(item_id: int) -> Optional[ArbiterItem]:
    for i in _load():
        if i["id"] == item_id:
            return ArbiterItem(**i)
    return None


def count_pending() -> int:
    return len(get_pending())


def resolve(item_id: int, status: str, note: str = "") -> Optional[ArbiterItem]:
    """Approve or deny an item. status: 'approved' | 'denied'."""
    with _lock:
        items = _load()
        for item in items:
            if item["id"] == item_id and item["status"] == "pending":
                item["status"] = status
                item["resolution_ts"] = datetime.now().isoformat()
                item["resolution_note"] = note
                _save(items)
                return ArbiterItem(**item)
    return None


def get_all(limit: int = 20) -> list[ArbiterItem]:
    """All items newest-first, up to limit."""
    items = _load()
    return [ArbiterItem(**i) for i in reversed(items[-limit:])]


def is_irreversible_impulse(content: str) -> bool:
    """Quick keyword scan — does this NE action impulse sound irreversible?"""
    lower = content.lower()
    return any(kw in lower for kw in IRREVERSIBLE_KEYWORDS)


def _ping_discord(item_id: int, description: str):
    """Best-effort Discord ping. One ping per item. Silently ignores errors."""
    try:
        channel_id_str = os.getenv("DISCORD_CHANNEL_ID", "").strip()
        if not channel_id_str:
            return
        from ..network import discord_bot

        discord_bot.send(
            int(channel_id_str),
            f"[Arbiter #{item_id}] Pending approval: {description[:140]}\n"
            f"Review with: /arbiter list",
        )
    except Exception as _bare_e:
        log_error(kind="BARE_EXCEPT", detail=f"wild_igor/igor/arbiter/queue.py: {_bare_e}")


# ── Tool registration ─────────────────────────────────────────────────────────


def _tool_arbiter_submit(
    description: str,
    context: str = "",
    action_type: str = "manual",
    threshold_reason: str = "",
    **_,
) -> str:
    """Igor calls this tool to queue an action for akien's approval."""
    item_id = submit(
        description=description,
        context=context,
        action_type=action_type,
        threshold_reason=threshold_reason
        or "Igor manually queued via arbiter_submit tool",
    )
    return (
        f"Arbiter item #{item_id} queued for Akien's review. "
        f"Akien can approve with: /arbiter approve {item_id}. "
        "Continue helping while waiting."
    )


registry.register(
    Tool(
        name="arbiter_submit",
        description=(
            "Queue an action for Akien's approval before executing it. "
            "Use for: irreversible actions (delete, send, publish, email), "
            "actions affecting systems Igor does not own, or any action "
            "where Igor is uncertain about Akien's preferences. "
            "Igor continues helping while Akien reviews the queue."
        ),
        parameters={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What action Igor was about to take",
                },
                "context": {
                    "type": "string",
                    "description": "Why Igor thinks this action is needed",
                },
                "action_type": {
                    "type": "string",
                    "enum": [
                        "irreversible",
                        "external_system",
                        "high_cost",
                        "ethics_flag",
                        "manual",
                    ],
                    "description": "Category of the action being queued",
                },
                "threshold_reason": {
                    "type": "string",
                    "description": "Which threshold criterion triggered the flag",
                },
            },
            "required": ["description"],
        },
        fn=_tool_arbiter_submit,
    )
)

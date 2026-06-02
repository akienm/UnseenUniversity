"""
dispatch.py — Dispatch functions for GrannyWeatherwaxDevice routing edges.

Each dispatch_fn takes a ticket dict and returns bool (True=dispatched).
The CC dispatch function:
  1. Calls cc_queue.py dispatch <ticket_id> to set the ticket in_progress
  2. Posts GRANNY_DISPATCH to the shared channel for observability
  3. Spawns a detached tmux session running 'claude -p /sprint-ticket <id>'

The inference dispatch function routes tickets to a cheap model via InferenceDevice:
  1. Calls cc_queue.py dispatch <ticket_id> to set in_progress
  2. Posts GRANNY_DISPATCH|worker=<task_class> to the shared channel
  3. Sends the ticket description to InferenceDevice
     - task_class='minion' for tickets tagged 'minion' (→ qwen via OR)
     - task_class='worker' for all others (→ deepseek-v4-flash via OR)
  4. Logs token cost at INFO level + posts INFERENCE_COST to channel
  5. Submits result via cc_queue.py done (awaiting_validation, not auto-close)
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Repo root — where CC must run so it picks up CLAUDE.md and project context.
_UU_ROOT = Path(__file__).parent.parent.parent.resolve()
# Always use UU's own cc_queue.py — never inherited CC_WORKFLOW_TOOLS which
# may point to the old TheIgors checkout.
_CC_QUEUE = _UU_ROOT / "lab" / "claudecode" / "cc_queue.py"
_PYTHON = sys.executable  # same venv interpreter that started the daemon

# Tags that indicate a ticket should use the cheap minion model (qwen).
# All other sprint tickets route to worker tier (deepseek-v4-flash).
_MINION_TAGS = frozenset({"minion"})


def cc_dispatch_fn(ticket: dict) -> bool:
    """Post GRANNY_DISPATCH to the channel for a CC ticket.

    Marks in_progress via cc_queue.py and posts the channel event.
    Actual CC.0 dispatch (send-keys to claude-main) is handled by
    T-granny-cc0-dispatch via the availability semaphore gate.
    """
    ticket_id = ticket.get("id", "")
    if not ticket_id:
        log.warning("cc_dispatch_fn: ticket has no id — skipping")
        return False

    # Mark in_progress via cc_queue
    try:
        result = subprocess.run(
            [_PYTHON, str(_CC_QUEUE), "dispatch", ticket_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 and "already" not in result.stderr.lower():
            log.warning(
                "cc_queue dispatch %s failed: %s", ticket_id, result.stderr[:200]
            )
    except Exception as e:
        log.warning("cc_dispatch_fn: cc_queue call failed for %s: %s", ticket_id, e)
        # Continue — channel post + CC launch matter more than the queue mark

    # Post to shared channel for observability — best-effort, never blocks launch
    try:
        from unseen_university.channel import post_to_channel

        title = ticket.get("title", "")[:60]
        size = ticket.get("size", "?")
        tags = ",".join(ticket.get("tags", []))
        msg = (
            f"GRANNY_DISPATCH|ticket={ticket_id}|worker=claude|size={size}"
            f"|tags={tags}|title={title}"
        )
        post_to_channel(msg, author="granny-weatherwax", channel="granny-weatherwax")
        log.info("cc_dispatch_fn: channel post OK for %s", ticket_id)
    except Exception as e:
        log.warning("cc_dispatch_fn: channel post failed for %s: %s", ticket_id, e)

    return True


# Tier cascade for OR routing: tickets try each tier in order before going to CC.
# minion-tagged tickets skip straight to minion (cheapest, simplest).
# All other tickets start at analyst (most capable OR tier) and fall back.
_OR_TIER_CASCADE = ("analyst", "worker", "minion")


def inference_dispatch_fn(ticket: dict, on_complete=None) -> bool:
    """Dispatch a ticket through the OR tier cascade: analyst → worker → minion → CC block.

    Each tier gets one full tool-loop run. On DONE, the ticket closes. On ESCALATE,
    the next tier runs with the full escalation_history attached so it can learn
    from the prior attempt. Only when all OR tiers are exhausted does the ticket
    block for CC review.

    minion-tagged tickets skip directly to the minion tier (single-tier run).

    on_complete: optional callable(worker_result, task_class, ticket) called after
        each tier's execute() — used by GrannyDaemon to record outcomes into
        PatternTracker.
    """
    ticket_id = ticket.get("id", "")
    if not ticket_id:
        log.warning("inference_dispatch_fn: ticket has no id — skipping")
        return False

    tags = set(ticket.get("tags", []))
    # minion-tagged → start at last (cheapest) tier; others start at analyst
    start_idx = len(_OR_TIER_CASCADE) - 1 if (tags & _MINION_TAGS) else 0
    tier_sequence = _OR_TIER_CASCADE[start_idx:]

    # Mark in_progress via cc_queue
    try:
        result = subprocess.run(
            [_PYTHON, str(_CC_QUEUE), "dispatch", ticket_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 and "already" not in result.stderr.lower():
            log.warning(
                "inference_dispatch %s: cc_queue dispatch failed: %s",
                ticket_id,
                result.stderr[:200],
            )
    except Exception as e:
        log.warning(
            "inference_dispatch_fn: cc_queue call failed for %s: %s", ticket_id, e
        )

    # Post initial dispatch event
    try:
        from unseen_university.channel import post_to_channel

        title = ticket.get("title", "")[:60]
        size = ticket.get("size", "?")
        tags_str = ",".join(ticket.get("tags", []))
        post_to_channel(
            f"GRANNY_DISPATCH|ticket={ticket_id}|worker={tier_sequence[0]}|size={size}"
            f"|tags={tags_str}|title={title}",
            author="granny-weatherwax",
            channel="granny-weatherwax",
        )
    except Exception as e:
        log.warning(
            "inference_dispatch_fn: channel post failed for %s: %s", ticket_id, e
        )

    # ── Tiered cascade ────────────────────────────────────────────────────────
    try:
        from devices.minion.device import MinionDevice
        from devices.minion.shim import WorkerEnvelope

        escalation_history: list[dict] = []
        total_cost_usd: float = 0.0
        description = (
            f"Title: {ticket.get('title', '')}\n\n{ticket.get('description', '')}"
        )

        for tier in tier_sequence:
            envelope = WorkerEnvelope(
                ticket_id=ticket_id,
                description=description,
                session_id=ticket_id,
                cwd=str(_UU_ROOT),
                task_class=tier,
                escalation_history=escalation_history,
            )
            worker_result = MinionDevice().execute(envelope)
            total_cost_usd += worker_result.cost_usd

            log.info(
                "inference_dispatch %s tier=%s: signal=%r iterations=%d "
                "cost_usd=%.4f total_usd=%.4f",
                ticket_id,
                tier,
                worker_result.signal,
                worker_result.iterations,
                worker_result.cost_usd,
                total_cost_usd,
            )

            if on_complete is not None:
                try:
                    on_complete(worker_result, tier, ticket)
                except Exception as e:
                    log.warning(
                        "inference_dispatch %s: on_complete callback failed: %s",
                        ticket_id,
                        e,
                    )

            # Post per-tier result for observability
            try:
                from unseen_university.channel import post_to_channel

                advisor_part = (
                    f"|advisor_signal={worker_result.advisor_signal}"
                    if worker_result.advisor_signal
                    else ""
                )
                post_to_channel(
                    f"MINION_RESULT|ticket={ticket_id}|signal={worker_result.signal}"
                    f"|tier={tier}|iterations={worker_result.iterations}"
                    f"|rounds={worker_result.round_count}"
                    f"{advisor_part}"
                    f"|cost_usd={worker_result.cost_usd:.4f}"
                    f"|total_cost_usd={total_cost_usd:.4f}"
                    f"|tokens_in={worker_result.input_tokens}"
                    f"|tokens_out={worker_result.output_tokens}",
                    author="granny-weatherwax",
                    channel="granny-weatherwax",
                )
            except Exception:
                pass

            if worker_result.signal == "DONE":
                summary = f"or-{tier}: {worker_result.notes[:200]}"
                try:
                    subprocess.run(
                        [_PYTHON, str(_CC_QUEUE), "done", ticket_id, summary],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                except Exception as e:
                    log.warning(
                        "inference_dispatch %s: done call failed: %s", ticket_id, e
                    )
                return True

            # ESCALATE — record history and try next tier
            escalation_history.append(
                {
                    "tier": tier,
                    "signal": worker_result.signal,
                    "notes": worker_result.notes[:300],
                    "iterations": worker_result.iterations,
                    "cost_usd": worker_result.cost_usd,
                }
            )
            try:
                from unseen_university.channel import post_to_channel

                post_to_channel(
                    f"OR_TIER_ESCALATE|ticket={ticket_id}|from={tier}"
                    f"|signal={worker_result.signal}|remaining={list(_OR_TIER_CASCADE[_OR_TIER_CASCADE.index(tier)+1:])}",
                    author="granny-weatherwax",
                    channel="granny-weatherwax",
                )
            except Exception:
                pass
            log.warning(
                "inference_dispatch %s: tier=%s → %s — escalating to next tier",
                ticket_id,
                tier,
                worker_result.signal,
            )

        # All OR tiers exhausted — hold for CC
        tier_summary = "; ".join(
            f"{h['tier']}={h['signal']}({h['notes'][:80]})" for h in escalation_history
        )
        hold_reason = (
            f"all OR tiers exhausted (total_cost=${total_cost_usd:.4f}): {tier_summary}"
        )
        log.warning(
            "inference_dispatch %s: all tiers exhausted — blocking for CC: %s",
            ticket_id,
            hold_reason,
        )
        try:
            subprocess.run(
                [_PYTHON, str(_CC_QUEUE), "block", ticket_id, hold_reason],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as e:
            log.warning("inference_dispatch %s: block call failed: %s", ticket_id, e)
        return True

    except Exception as e:
        log.error("inference_dispatch %s: cascade failed: %s", ticket_id, e)
        return False

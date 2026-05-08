"""
habit_health_audit.py — Conversation health diagnostic tool.

Reads turn traces and channel messages to detect:
  1. Bare responses — response < 30 chars with no tool dispatch
  2. Habit misfires — habit fired on wrong intent (e.g. PROC_DISK_USAGE_CHECK on conversation)
  3. Thread drops — topic changed without resolution
  4. "Any thoughts?" prompts — user had to prompt for engagement
  5. Intent misclassification — thalamus classified statement as complaint/general when
     it warranted engagement
  6. TWM noise ratio — system entries drowning conversation entries

Output: structured report dict, suitable for logging, MCP return, or Igor self-review.

Registered as tool: audit_conversation_health(hours=24)
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Patterns for detection ────────────────────────────────────────────────────

# User prompts that indicate Igor failed to engage
_ANY_THOUGHTS_PATTERNS = re.compile(
    r"(?i)\b(?:"
    r"any thoughts|what do you think|thoughts on that|your take|"
    r"what about that|any ideas|what say you|your thoughts|"
    r"opinions?\??|react(?:ion)?s?\??|"
    r"does that (?:make sense|land|resonate)|"
    r"how does that (?:sit|sound|strike you)|"
    r"anything to add"
    r")\b"
)

# Habits that are commonly system/diagnostic — misfiring on conversation
_SYSTEM_HABITS = {
    "PROC_DISK_USAGE_CHECK",
    "PROC_CLUSTER_SSH_CHECK",
    "PROC_CHECK_PROCESS",
    "PROC_RESOURCE_AWARENESS",
    "PROC_TASK_SUPPRESS_STALE",
}

# Intents where a habit firing is suspicious (user is talking, not commanding)
_CONVERSATIONAL_INTENTS = {
    "conversation",
    "general",
    "greeting",
    "meta_question",
    "explanation_request",
}

# Intent classifications that suggest misclassification of a statement
_SUSPECT_INTENT_FOR_STATEMENT = {
    "complaint",
    "action_request",
    "tool_use",
}

# Bare response: very short, no substance
BARE_RESPONSE_THRESHOLD = 30


# ── Turn trace parsing ────────────────────────────────────────────────────────


def _find_trace_dir() -> Path:
    """Find the directory containing turn_trace logs."""
    from ..paths import paths as _paths

    # Check local logs first (current instance)
    local = _paths().logs
    if local.exists() and list(local.glob("turn_trace.*.log")):
        return local
    # Fall back to instance logs
    inst = _paths().instance / "logs"
    if inst.exists():
        return inst
    # Fall back to top-level logs
    top = _paths().runtime / "logs"
    return top


def _parse_turn_traces(hours: int = 24) -> list[dict]:
    """Parse turn trace logs from the last N hours. Returns list of turn dicts."""
    trace_dir = _find_trace_dir()
    cutoff = datetime.now() - timedelta(hours=hours)
    turns = []

    # Collect log files that might be in range
    for log_file in sorted(trace_dir.glob("turn_trace.*.log")):
        date_str = log_file.stem.split(".")[-1]
        try:
            file_date = datetime.strptime(date_str, "%Y%m%d")
            if file_date.date() < cutoff.date():
                continue
        except ValueError:
            continue

        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Split into turn blocks
        blocks = re.split(r"\n=== turn ", text)
        for block in blocks[1:]:
            try:
                json_start = block.index("{")
                json_end = block.rindex("}") + 1
                turn = json.loads(block[json_start:json_end])

                # Filter by time
                ts = turn.get("ts", "")
                if ts:
                    try:
                        turn_time = datetime.fromisoformat(ts)
                        if turn_time < cutoff:
                            continue
                    except ValueError as _exc:
                        from ..cognition.forensic_logger import log_error as _le
                        _le(kind="SILENT_EXCEPT", detail=f"habit_health_audit.py:127: {_exc}")

                turns.append(turn)
            except (ValueError, json.JSONDecodeError):
                continue

    return turns


# ── Channel message reading ───────────────────────────────────────────────────


def _read_channel_messages(hours: int = 24) -> list[dict]:
    """Read channel messages from Postgres or JSONL fallback."""
    messages = []

    # Try Postgres first
    db_url = os.environ.get("IGOR_HOME_DB_URL")
    if db_url:
        try:
            import psycopg2

            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
            conn = psycopg2.connect(db_url)
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT ts, author, type, content FROM channel_messages "
                    "WHERE ts > %s ORDER BY ts ASC",
                    (cutoff,),
                )
                for row in cur.fetchall():
                    messages.append(
                        {
                            "ts": row[0],
                            "author": row[1],
                            "type": row[2],
                            "content": row[3],
                        }
                    )
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Postgres channel read failed: %s", e)

    # Fallback to JSONL if no Postgres results
    if not messages:
        jsonl = Path(os.path.expanduser("~/.TheIgors/cc_channel/messages.jsonl"))
        if jsonl.exists():
            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
            try:
                for line in jsonl.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                        if msg.get("ts", "") > cutoff:
                            messages.append(msg)
                    except json.JSONDecodeError:
                        continue
            except Exception as _exc:
                from ..cognition.forensic_logger import log_error as _le
                _le(kind="SILENT_EXCEPT", detail=f"habit_health_audit.py:188: {_exc}")

    return messages


# ── Detection functions ───────────────────────────────────────────────────────


def _detect_bare_responses(turns: list[dict]) -> list[dict]:
    """Find turns where Igor's response was suspiciously short."""
    findings = []
    for turn in turns:
        resp = turn.get("response", {})
        preview = resp.get("preview", "")
        habit_fired = resp.get("habit_fired", False)

        if len(preview) < BARE_RESPONSE_THRESHOLD and habit_fired:
            findings.append(
                {
                    "type": "bare_response",
                    "turn_id": turn.get("turn_id"),
                    "ts": turn.get("ts"),
                    "input": turn.get("input", "")[:100],
                    "response": preview,
                    "habit": turn.get("bg_scoring", {}).get("winner", "?"),
                    "intent": turn.get("thalamus", {}).get("intent", "?"),
                }
            )
    return findings


def _detect_habit_misfires(turns: list[dict]) -> list[dict]:
    """Find turns where a system habit fired on conversational input."""
    findings = []
    for turn in turns:
        bg = turn.get("bg_scoring", {})
        winner = bg.get("winner", "")
        intent = turn.get("thalamus", {}).get("intent", "")
        input_text = turn.get("input", "")

        # System habit firing on conversational intent
        if winner in _SYSTEM_HABITS and intent in _CONVERSATIONAL_INTENTS:
            findings.append(
                {
                    "type": "habit_misfire_system_on_conversation",
                    "turn_id": turn.get("turn_id"),
                    "ts": turn.get("ts"),
                    "input": input_text[:100],
                    "winner": winner,
                    "winner_score": bg.get("winner_score"),
                    "intent": intent,
                    "response": turn.get("response", {}).get("preview", "")[:80],
                }
            )

        # Suspect intent classification — check if input looks conversational
        # but got classified as complaint/action_request
        if intent in _SUSPECT_INTENT_FOR_STATEMENT:
            # Check if input is from a human (not system/bg)
            if "[Web message from" in input_text or "TALKING WITH:" in input_text:
                # No question mark, no imperative verb = probably a statement,
                # not a complaint or action request
                user_text = (
                    input_text.split("]:")[-1].strip()
                    if "]:" in input_text
                    else input_text
                )
                if "?" not in user_text and len(user_text) > 20:
                    findings.append(
                        {
                            "type": "suspect_intent_classification",
                            "turn_id": turn.get("turn_id"),
                            "ts": turn.get("ts"),
                            "input": input_text[:100],
                            "classified_as": intent,
                            "winner": winner,
                            "response": turn.get("response", {}).get("preview", "")[
                                :80
                            ],
                        }
                    )

    return findings


def _detect_thread_drops(turns: list[dict]) -> list[dict]:
    """Find consecutive turns where the topic changed without resolution.

    Heuristic: if turn N has a substantive topic and turn N+1's response
    doesn't reference any words from N's input, it's a potential thread drop.
    """
    findings = []
    for i in range(len(turns) - 1):
        curr = turns[i]
        next_t = turns[i + 1]

        # Only check human-to-human conversation turns
        curr_input = curr.get("input", "")
        next_input = next_t.get("input", "")
        if "[Web message from" not in curr_input:
            continue

        # Extract user words from current turn
        user_text = curr_input.split("]:")[-1].strip() if "]:" in curr_input else ""
        if len(user_text) < 20:
            continue  # too short to be a substantive topic

        # Check if Igor's response (current turn) relates to the input
        curr_response = curr.get("response", {}).get("preview", "")
        curr_words = set(user_text.lower().split())
        resp_words = set(curr_response.lower().split())

        # If response shares < 15% of input words, possible thread drop
        if curr_words:
            overlap = len(curr_words & resp_words) / len(curr_words)
            if overlap < 0.15 and len(curr_response) > 10:
                findings.append(
                    {
                        "type": "thread_drop",
                        "turn_id": curr.get("turn_id"),
                        "ts": curr.get("ts"),
                        "input": curr_input[:100],
                        "response": curr_response[:80],
                        "word_overlap": round(overlap, 2),
                        "winner": curr.get("bg_scoring", {}).get("winner", "?"),
                    }
                )

    return findings


def _detect_any_thoughts_prompts(turns: list[dict]) -> list[dict]:
    """Find turns where the user had to prompt Igor for engagement."""
    findings = []
    for i, turn in enumerate(turns):
        input_text = turn.get("input", "")
        if _ANY_THOUGHTS_PATTERNS.search(input_text):
            # Look back at the previous turn to see what Igor failed to engage with
            prev_context = ""
            if i > 0:
                prev = turns[i - 1]
                prev_context = prev.get("response", {}).get("preview", "")[:80]

            findings.append(
                {
                    "type": "any_thoughts_prompt",
                    "turn_id": turn.get("turn_id"),
                    "ts": turn.get("ts"),
                    "prompt": input_text[:100],
                    "preceding_response": prev_context,
                    "preceding_intent": (
                        turns[i - 1].get("thalamus", {}).get("intent", "?")
                        if i > 0
                        else "?"
                    ),
                }
            )

    return findings


def _detect_twm_noise(turns: list[dict]) -> dict:
    """Analyze TWM state indicators from turn traces.

    Returns aggregate stats rather than per-turn findings.
    """
    total_turns = len(turns)
    if total_turns == 0:
        return {"total_turns": 0}

    # Count turns by routing tier — lots of tier.2 on conversation = under-serving
    tier_counts = {}
    intent_counts = {}
    habit_fire_count = 0
    system_habit_count = 0

    for turn in turns:
        resp = turn.get("response", {})
        tier = resp.get("tier", "unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        intent = turn.get("thalamus", {}).get("intent", "unknown")
        intent_counts[intent] = intent_counts.get(intent, 0) + 1

        if resp.get("habit_fired"):
            habit_fire_count += 1
            winner = turn.get("bg_scoring", {}).get("winner", "")
            if winner in _SYSTEM_HABITS:
                system_habit_count += 1

    return {
        "total_turns": total_turns,
        "tier_distribution": tier_counts,
        "intent_distribution": intent_counts,
        "habit_fire_rate": (
            round(habit_fire_count / total_turns, 2) if total_turns else 0
        ),
        "system_habit_rate": (
            round(system_habit_count / total_turns, 2) if total_turns else 0
        ),
    }


# ── Main audit function ──────────────────────────────────────────────────────


def audit_conversation_health(hours: int = 24) -> dict:
    """
    Run a full conversation health audit over the last N hours.

    Returns a structured report with findings by category and aggregate stats.
    Registered as Igor tool: audit_conversation_health(hours=24)
    """
    logger.info("Running conversation health audit for last %d hours", hours)

    turns = _parse_turn_traces(hours)
    # channel_msgs = _read_channel_messages(hours)  # available for future enrichment

    bare = _detect_bare_responses(turns)
    misfires = _detect_habit_misfires(turns)
    thread_drops = _detect_thread_drops(turns)
    any_thoughts = _detect_any_thoughts_prompts(turns)
    twm_stats = _detect_twm_noise(turns)

    # Separate misfire types
    system_misfires = [
        f for f in misfires if f["type"] == "habit_misfire_system_on_conversation"
    ]
    intent_issues = [
        f for f in misfires if f["type"] == "suspect_intent_classification"
    ]

    report = {
        "audit_ts": datetime.now().isoformat(),
        "window_hours": hours,
        "turns_analyzed": len(turns),
        "summary": {
            "bare_responses": len(bare),
            "habit_misfires": len(system_misfires),
            "intent_misclassifications": len(intent_issues),
            "thread_drops": len(thread_drops),
            "any_thoughts_prompts": len(any_thoughts),
        },
        "twm_stats": twm_stats,
        "findings": {
            "bare_responses": bare[:10],  # cap at 10 per category
            "habit_misfires": system_misfires[:10],
            "intent_misclassifications": intent_issues[:10],
            "thread_drops": thread_drops[:10],
            "any_thoughts_prompts": any_thoughts[:10],
        },
    }

    # Log summary
    total_issues = sum(report["summary"].values())
    logger.info(
        "Conversation health audit: %d turns, %d issues found "
        "(bare=%d, misfires=%d, intent=%d, drops=%d, prompts=%d)",
        len(turns),
        total_issues,
        len(bare),
        len(system_misfires),
        len(intent_issues),
        len(thread_drops),
        len(any_thoughts),
    )

    return report


def format_report(report: dict) -> str:
    """Format the audit report as human-readable text."""
    lines = [
        f"=== Conversation Health Audit — {report['audit_ts'][:19]} ===",
        f"Window: {report['window_hours']}h | Turns: {report['turns_analyzed']}",
        "",
        "SUMMARY:",
    ]

    s = report["summary"]
    for key, count in s.items():
        label = key.replace("_", " ").title()
        marker = "!!" if count > 0 else "ok"
        lines.append(f"  [{marker}] {label}: {count}")

    # TWM stats
    twm = report.get("twm_stats", {})
    lines.append("")
    lines.append("TWM STATS:")
    lines.append(f"  Habit fire rate: {twm.get('habit_fire_rate', '?')}")
    lines.append(f"  System habit rate: {twm.get('system_habit_rate', '?')}")
    if twm.get("tier_distribution"):
        lines.append(f"  Tier distribution: {twm['tier_distribution']}")
    if twm.get("intent_distribution"):
        lines.append(f"  Intent distribution: {twm['intent_distribution']}")

    # Top findings
    findings = report.get("findings", {})
    for category, items in findings.items():
        if not items:
            continue
        lines.append("")
        lines.append(f"--- {category.replace('_', ' ').upper()} ---")
        for item in items[:5]:  # show top 5
            lines.append(
                f"  [{item.get('ts', '?')[:19]}] turn={item.get('turn_id', '?')}"
            )
            if "input" in item:
                lines.append(f"    input: {item['input'][:80]}")
            if "winner" in item:
                lines.append(f"    habit: {item['winner']}")
            if "intent" in item or "classified_as" in item:
                lines.append(
                    f"    intent: {item.get('intent', item.get('classified_as', '?'))}"
                )
            if "response" in item:
                lines.append(f"    response: {item['response'][:60]}")
            if "prompt" in item:
                lines.append(f"    prompt: {item['prompt'][:80]}")
            if "preceding_response" in item:
                lines.append(f"    preceding: {item['preceding_response'][:60]}")
            lines.append("")

    return "\n".join(lines)


# ── Tool wrapper for Igor dispatch ────────────────────────────────────────────


def _tool_audit_conversation_health(hours: str = "24") -> str:
    """Tool-compatible wrapper: returns formatted text report."""
    try:
        h = int(hours)
    except (ValueError, TypeError):
        h = 24
    report = audit_conversation_health(h)
    return format_report(report)


# ── Registration ──────────────────────────────────────────────────────────────

from lab.utility_closet.registry import Tool, registry  # noqa: E402

registry.register(
    Tool(
        name="audit_conversation_health",
        description=(
            "Run a conversation health audit over the last N hours. "
            "Detects: bare responses, habit misfires, thread drops, "
            "'any thoughts' prompts (where user had to prompt for engagement), "
            "intent misclassification, TWM noise ratio. "
            "Optional: hours (default 24)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "hours": {
                    "type": "string",
                    "description": "Number of hours to audit (default 24)",
                }
            },
            "required": [],
        },
        fn=_tool_audit_conversation_health,
    )
)

"""
urgency_provenance.py — T-urgency-provenance

Answers "why is that important?" by tracing the provenance of hot TWM items
and de-emphasizing anything whose urgency isn't grounded in actual content.

Design principle (per Akien, 2026-04-04): urgency must come from content,
not from the delivery mechanism. Igor is built to resist phone-addiction
dynamics, not replicate them. If a salience signal can't show its work, it
should dissolve under examination.

Grounded sources — urgency is real:
  stdin, web, web_message, user_input, inbox (with explicit keyword),
  goal, task, explicit_request, human_authored

Manufactured sources — urgency is system noise:
  inbox_watcher (default, no explicit keyword), milieu_source, milieu_gradient,
  interoception_source, scheduler_source, proactive_habit, twm_trigger,
  ne_diagnostic, inbox_watcher (fallback), heartbeat, boot_check
"""

import logging

from lab.utility_closet.registry import Tool, registry

logger = logging.getLogger(__name__)

# Sources whose urgency is considered grounded (came from actual content/intent)
_GROUNDED_SOURCES = frozenset(
    {
        "stdin",
        "web",
        "web_message",
        "user_input",
        "explicit_request",
        "goal",
        "task",
        "human_authored",
        "claude-code",
        "akien",
    }
)

# Urgency range to lower: don't touch items at or above 0.65 (explicit high-urgency)
_LOWER_MIN = 0.31  # below this is already quiet — leave alone
_LOWER_MAX = 0.64  # at or above 0.65 = user explicitly flagged urgent


def trace_urgency_provenance(**_) -> str:
    """
    Read the current hot TWM items, classify each by source provenance,
    and lower urgency on items that have no content-grounded reason to be urgent.

    Returns a plain-text report of what was found and what was adjusted.
    """
    try:
        from ..memory.cortex import Cortex as _Cortex

        cortex = _Cortex()
    except Exception as e:
        return f"urgency-provenance: cortex unavailable — {e}"

    try:
        items = cortex.twm_read(limit=20)
    except Exception as e:
        return f"urgency-provenance: twm_read failed — {e}"

    if not items:
        return "urgency-provenance: TWM is empty."

    # Sort by urgency * salience descending — hottest first
    items = sorted(items, key=lambda r: r["urgency"] * r["salience"], reverse=True)

    grounded = []
    manufactured = []
    lowered = []

    for item in items:
        obs_id = item["id"]
        source = item.get("source", "unknown")
        urgency = item.get("urgency", 0.2)
        salience = item.get("salience", 0.2)
        content = item.get("content_csb", "")[:80]

        # Classify: grounded if source matches known grounded set,
        # or if source starts with a grounded prefix (e.g. web:session-id)
        is_grounded = source in _GROUNDED_SOURCES or any(
            source.startswith(g) for g in _GROUNDED_SOURCES
        )

        if is_grounded:
            grounded.append(
                f"  ✓ [{source}] urgency={urgency:.2f} salience={salience:.2f}  {content}"
            )
        else:
            manufactured.append(
                f"  ~ [{source}] urgency={urgency:.2f} salience={salience:.2f}  {content}"
            )
            # Lower urgency if in the "manufactured noise" range
            if _LOWER_MIN <= urgency <= _LOWER_MAX:
                try:
                    cortex.twm_lower_urgency(obs_id, new_urgency=0.2)
                    lowered.append(f"  ↓ [{source}] obs_id={obs_id}  {content[:60]}")
                except Exception as e:
                    logger.warning(
                        "urgency_provenance: lower failed obs_id=%s: %s", obs_id, e
                    )

    lines = ["URGENCY PROVENANCE REPORT"]
    lines.append(f"  {len(items)} TWM items examined")
    lines.append(
        f"  {len(grounded)} grounded (content-sourced), {len(manufactured)} manufactured (system noise)"
    )

    if grounded:
        lines.append("\nGrounded (keep):")
        lines.extend(grounded)

    if manufactured:
        lines.append("\nManufactured (system-generated urgency):")
        lines.extend(manufactured)

    if lowered:
        lines.append(
            f"\nDe-emphasized ({len(lowered)} items — urgency lowered to 0.2):"
        )
        lines.extend(lowered)
    else:
        lines.append("\nNo urgency adjustments needed.")

    return "\n".join(lines)


registry.register(
    Tool(
        name="trace_urgency_provenance",
        description=(
            "Trace the provenance of current hot TWM items. "
            "Classifies each as grounded (content-sourced) or manufactured (system noise). "
            "Automatically lowers urgency on manufactured items that have no content-grounded reason. "
            "Invoke when asked 'why is that important?' or 'is that actually urgent?'."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=trace_urgency_provenance,
    )
)

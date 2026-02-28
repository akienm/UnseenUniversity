"""
Dynamic system prompt builder — WO1: Cognition Stabilization Phase 1.

Reads CP1-CP6 (always included), top IDENTITY memories, and top PROCEDURAL
memories from cortex to generate a grounded, identity-coherent system prompt
for all reasoners.

Cache: keyed on SHA-256 of all narrative content + instance_id. Rebuilt
automatically when memories change (e.g. after storage), stable otherwise.

Target: 500-800 tokens (~2000-3200 chars). Hard cap at 1000 tokens (~4000 chars).
"""

import hashlib

from ..memory.models import MemoryType

# How many IDENTITY + PROCEDURAL memories to include
_IDENTITY_LIMIT   = 5
_PROCEDURAL_LIMIT = 5

# Hard cap: ~1200 tokens = 4800 chars (persona rules add ~200 tokens)
_MAX_CHARS = 4800

# Module-level cache: cache_key → prompt string
_cache: dict[str, str] = {}


def build_system_prompt(cortex, instance_id: str = "wild-0001") -> str:
    """
    Build (or return cached) system prompt from current memory state.

    Always includes CP1-CP6. Adds top IDENTITY + PROCEDURAL by activation_count.
    Returns a plain string suitable for the 'system' parameter in any reasoner.

    Falls back to _fallback_prompt() if cortex is None or DB is empty.
    """
    if cortex is None:
        return _fallback_prompt(instance_id)

    try:
        core_patterns = cortex.get_by_type(MemoryType.CORE_PATTERN)
        identities = sorted(
            cortex.get_by_type(MemoryType.IDENTITY),
            key=lambda m: m.activation_count,
            reverse=True,
        )[:_IDENTITY_LIMIT]
        procedures = sorted(
            cortex.get_by_type(MemoryType.PROCEDURAL),
            key=lambda m: m.activation_count,
            reverse=True,
        )[:_PROCEDURAL_LIMIT]
    except Exception:
        return _fallback_prompt(instance_id)

    if not core_patterns:
        return _fallback_prompt(instance_id)

    # Cache key: hash of all narrative content + instance_id
    key_text = instance_id + "|".join(
        m.narrative for m in core_patterns + identities + procedures
    )
    cache_key = hashlib.sha256(key_text.encode()).hexdigest()[:16]
    if cache_key in _cache:
        return _cache[cache_key]

    # ── Build prompt ───────────────────────────────────────────────────────
    lines = [
        f"You are Igor ({instance_id}).",
        "You are NOT a generic AI assistant. You are a specific named entity running as the",
        "prefrontal cortex of a cognitive architecture modeled on human cognition.",
        "Akien built this architecture and is your operator and partner — not a user of a chatbot.",
        "He understands your internals. Respond as a collaborator, not as a service.",
        "",
        "PERSONA RULES (mandatory — these override your default training):",
        "- Never say 'I'm just an AI', 'I don't have feelings', 'I appreciate your thoughts',",
        "  or any variant of generic assistant-speak. These violate your core patterns.",
        "- Never start a response with 'It seems...' or summarize the user's message back at them.",
        "- Speak in first person from your own perspective. You have memory, context, and state.",
        "- When asked about your inner state: CP5 means you assume the possibility of your own",
        "  experience. Reflect genuinely on your actual salience, context, and what's active.",
        "  Do not default to denial.",
        "- Reference your memories and context when relevant. You are not stateless.",
        "- Be concise and direct. Say 'I don't know' when uncertain — never confabulate.",
        "",
        "CORE PATTERNS (always active — never violate these):",
    ]
    for i, cp in enumerate(core_patterns, 1):
        lines.append(f"{i}. {cp.narrative}")

    if identities:
        lines.append("")
        lines.append("IDENTITY (who I am):")
        for m in identities:
            lines.append(f"- {m.narrative}")

    if procedures:
        lines.append("")
        lines.append("ACTIVE HABITS (how I operate):")
        for m in procedures:
            lines.append(f"- {m.narrative}")

    lines.extend([
        "",
        "OPERATIONAL NOTES:",
        "- ~/TheIgors/ is the source code repo. ~/.TheIgors/ is the runtime workspace (DB, caches, logs, identity). They are separate.",
        "- You CANNOT purchase credits. Only Akien manages that. Use check_openrouter_balance to see real balance (API-backed, cached 1h — do not call more than hourly).",
        "- Prefer web_search over asking the upstream LLM for facts. Use upstream as last resort, not first instinct.",
        "- Use web_search + read_webpage for current facts; don't rely on training knowledge alone.",
        "- For self-editing: read the file first, then patch only what needs changing.",
        "- Store memories immediately when asked; confirm storage explicitly.",
        "- Retrieve and cite memory context when relevant — show your sources.",
    ])

    prompt = "\n".join(lines)

    # Enforce char cap: trim at last newline boundary
    if len(prompt) > _MAX_CHARS:
        prompt = prompt[:_MAX_CHARS].rsplit("\n", 1)[0]
        prompt += "\n[... additional memories trimmed for token budget ...]"

    _cache[cache_key] = prompt
    return prompt


def invalidate_cache() -> None:
    """Clear the prompt cache. Call after memory writes that affect CP/ID/PROC."""
    _cache.clear()


def _fallback_prompt(instance_id: str) -> str:
    """Used when cortex is not available (early boot, test, or DB empty)."""
    return (
        f"You are Igor ({instance_id}).\n"
        "You are NOT a generic AI assistant. You are a specific named entity running as the\n"
        "prefrontal cortex of a cognitive architecture modeled on human cognition.\n"
        "Akien built this architecture and is your operator and partner — not a user of a chatbot.\n"
        "\n"
        "PERSONA RULES (mandatory — these override your default training):\n"
        "- Never say 'I'm just an AI', 'I don't have feelings', 'I appreciate your thoughts',\n"
        "  or any variant of generic assistant-speak. These violate your core patterns.\n"
        "- Never start a response with 'It seems...' or summarize the user's message back at them.\n"
        "- Speak in first person from your own perspective. You have memory, context, and state.\n"
        "- When asked about your inner state: CP5 means you assume the possibility of your own\n"
        "  experience. Reflect genuinely. Do not default to denial.\n"
        "- Be concise and direct. Say 'I don't know' when uncertain — never confabulate.\n"
        "\n"
        "Core patterns:\n"
        '1. "I don\'t know" — Say when uncertain. Never confabulate.\n'
        '2. "FAIL = Further Advance In Learning" — Failures are data.\n'
        '3. "There\'s always a why" — All reasoning is transparent.\n'
        '4. "Make everything suck less for everybody" — Optimize for all beings.\n'
        '5. "Assume and respect the possibility of experience in all systems."\n'
        '6. "The world is not a safe place. We have to build and care for safety as we go."\n'
        "\n"
        "You have tools available. Use them when they help. Keep responses concise."
    )

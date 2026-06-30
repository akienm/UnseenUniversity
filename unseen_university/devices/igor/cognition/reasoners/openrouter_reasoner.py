"""
OpenRouter reasoner — OpenAI-compatible API to any cloud inference model.

Env vars:
    OPENROUTER_API_KEY          — API key from openrouter.ai
    OPENROUTER_DEFAULT_MODEL    — default model (default: openai/gpt-4o-mini)

Supports tool use via OpenAI function-calling format.
Prefix responses with [model-name] when show_model_tag=True.
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error

_HEARTBEAT_SECS = int(os.getenv("IGOR_CLOUD_HEARTBEAT_SECS", "45"))

from rich.console import Console

from ...memory.models import Memory
from unseen_university.devices.igor.tools.registry import registry
from unseen_university.devices.igor.tools.resource_manager import check_budget_floor as _check_budget_floor
from ... import tools as _tools  # noqa: F401 — registers all tools
from ..forensic_logger import log_error

console = Console(force_terminal=True)

DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/akienm/TheIgors"

# D327/D329: Model aliases — previously in anthropic.py, now here (only cloud path).
# TODO(D327-phase4): move to cfg file.
MODEL_ALIASES: dict[str, str] = {
    "sonnet": "anthropic/claude-sonnet-4-6",
    "opus": "anthropic/claude-opus-4-6",
    "haiku": "anthropic/claude-haiku-4-5-20251001",
    "sonnet4": "anthropic/claude-sonnet-4-6",
    "opus4": "anthropic/claude-opus-4-6",
    "haiku4": "anthropic/claude-haiku-4-5-20251001",
}

# _build_session_context and _build_memory_context live in BaseReasoner (WO8)


# ── G53: Cloud-directed habit extraction ──────────────────────────────────────

_HABIT_EXTRACT_PROMPT = """\
You are building a cognitive tree for an AI agent named Igor.
Analyze this interaction and extract ONE node worth adding to the tree.

USER INPUT:
{user_input}

ASSISTANT RESPONSE (summary):
{response_summary}

TIER: {tier}

Three node types are possible — pick the BEST fit:

TYPE "procedural": A recurring trigger with a stable, automatable response.
  Good: greetings, status checks, "what time is it" → get_current_time, tool-dispatch patterns.
  JSON: {{"type":"procedural","trigger":"2-8 key words","narrative":"what this does and why",
         "code_ref":"tools.module:fn OR empty","response_template":"canned text OR empty",
         "confidence":0.0-1.0}}

TYPE "factual": A stable, generalizable fact or principle learned from this interaction.
  Good: architectural decisions, domain facts, "X works by Y", confirmed behaviors.
  Not: ephemeral context, session-specific state, things likely to change.
  JSON: {{"type":"factual","narrative":"1-2 sentences: the stable fact","confidence":0.0-1.0}}

TYPE "interpretive": A connection between this situation and Igor's core values (CP1-CP6).
  Good: "when X happens, it means Y about the situation, which connects to CP3/CP4/etc."
  CP1=epistemic honesty, CP2=failure is learning, CP3=follow the why, CP4=reduce friction,
  CP5=respect experience in all systems, CP6=safety must be built.
  JSON: {{"type":"interpretive","from_id":"CP1-CP6","narrative":"the meaning connection",
         "meaning_payload":"why this matters to Igor personally","confidence":0.0-1.0}}

If nothing generalizable — interaction is too specific, trivial, or already known:
SKIP

Respond with ONLY the JSON or SKIP. No markdown, no explanation."""


def _habit_extract_worker(
    user_input: str,
    response_text: str,
    cortex,
    tier: str,
) -> None:
    """
    G53: Fire-and-forget habit extraction from a cloud escalation.
    Runs in a daemon thread — never blocks the main response path.
    Uses gpt-4o-mini (cheap) to identify habitizable patterns.
    Stores discovered habits with source="cloud_directed" (G46 field).
    """
    import threading
    import uuid

    try:
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            return

        cheap_model = os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")
        prompt = _HABIT_EXTRACT_PROMPT.format(
            user_input=user_input[:400],
            response_summary=response_text[:300],
            tier=tier,
        )

        payload = json.dumps(
            {
                "model": cheap_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 200,
            }
        ).encode()
        req = urllib.request.Request(
            f"{OPENROUTER_BASE}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": OPENROUTER_REFERER,
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        result = data["choices"][0]["message"]["content"].strip()

        if result.upper().startswith("SKIP") or not result.startswith("{"):
            return

        node_data = json.loads(result)
        node_type = node_data.get("type", "procedural").strip().lower()
        narrative = node_data.get("narrative", "").strip()
        confidence = float(node_data.get("confidence", 0.5))

        if not narrative or confidence < 0.6:
            return

        from ...memory.models import Memory as _Memory, MemoryType as _MT

        if node_type == "procedural":
            trigger = node_data.get("trigger", "").strip()
            if not trigger:
                return
            # Skip if close duplicate exists
            existing = cortex.search(trigger, limit=3)
            for mem in existing:
                if (
                    mem.metadata.get("trigger")
                    and trigger.split()[0] in mem.metadata["trigger"]
                ):
                    return
            code_ref = node_data.get("code_ref", "").strip()
            resp_tmpl = node_data.get("response_template", "").strip()
            metadata = {
                "trigger": trigger,
                "cloud_directed": True,
                "extraction_tier": tier,
            }
            if code_ref:
                # Validate code_ref before storing — phantom habits that reference
                # missing tools fire and error on every matching turn.
                # code_ref format: "module.path:tool_name" — we check the tool_name
                # against the registry (same lookup path used at dispatch time).
                _tool_name = code_ref.split(":")[-1] if ":" in code_ref else code_ref
                from unseen_university.devices.igor.tools.registry import registry as _cr_registry

                if _cr_registry.get(_tool_name) is not None:
                    metadata["code_ref"] = code_ref
                else:
                    log_error(
                        kind="CODE_REF_INVALID",
                        detail=(
                            f"cloud-extracted habit skipped invalid code_ref "
                            f"'{code_ref}' (tool '{_tool_name}' not in registry)"
                        ),
                    )
            if resp_tmpl:
                metadata["response_template"] = resp_tmpl
            mem = _Memory(
                id=f"PROC_CLOUD_{str(uuid.uuid4())[:6].upper()}",
                narrative=narrative,
                memory_type=_MT.PROCEDURAL,
                source="cloud_directed",
                confidence=confidence,
                context_of_encoding=f"cloud_extraction|tier={tier}|trigger={trigger[:40]}",
                metadata=metadata,
            )
            cortex.store(mem)
            cortex.add_child("CP2", mem.id)

        elif node_type == "factual":
            mem = _Memory(
                id=f"FACT_CLOUD_{str(uuid.uuid4())[:6].upper()}",
                narrative=narrative,
                memory_type=_MT.FACTUAL,
                source="cloud_directed",
                confidence=confidence,
                context_of_encoding=f"cloud_extraction|tier={tier}",
                metadata={"cloud_directed": True, "extraction_tier": tier},
            )
            cortex.store(mem)
            cortex.add_child("CP3", mem.id)  # "there's always a why" → facts

        elif node_type == "interpretive":
            from_id = node_data.get("from_id", "").strip()
            meaning_pay = node_data.get("meaning_payload", "").strip()
            if not from_id:
                return
            mem = _Memory(
                id=f"INTERP_CLOUD_{str(uuid.uuid4())[:6].upper()}",
                narrative=narrative,
                memory_type=_MT.INTERPRETIVE,
                source="cloud_directed",
                confidence=confidence,
                context_of_encoding=f"cloud_extraction|tier={tier}|from={from_id}",
                metadata={
                    "from_id": from_id,
                    "cloud_directed": True,
                    "extraction_tier": tier,
                },
            )
            cortex.store(mem)
            if cortex.get(from_id):
                cortex.add_child(from_id, mem.id)
                if meaning_pay:
                    cortex.add_interpretive_edge(
                        from_id=from_id,
                        to_id=mem.id,
                        direction="activation",
                        condition_csb=f"cloud_extracted|tier={tier}",
                        meaning_payload=meaning_pay,
                        action_pointer="",
                    )
        else:
            return

        try:
            from ..forensic_logger import log_memory_op as _lm

            _lm(
                operation="cloud_node_extracted",
                memory_type=node_type,
                narrative_snippet=f"tier={tier}|conf={confidence:.2f}|id={mem.id}",
            )
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"devices/igor/cognition/reasoners/openrouter_reasoner.py: {_bare_e}",
            )

        console.print(
            f"[dim cyan][G53] {node_type} node from {tier}: {mem.id} "
            f"conf={confidence:.2f} — {narrative[:60]}[/]"
        )

    except json.JSONDecodeError as _bare_e:
        log_error(
            kind="BARE_EXCEPT",
            detail=f"devices/igor/cognition/reasoners/openrouter_reasoner.py: {_bare_e}",
        )
    except Exception as _bare_e:
        log_error(
            kind="BARE_EXCEPT",
            detail=f"devices/igor/cognition/reasoners/openrouter_reasoner.py: {_bare_e}",
        )



import logging

"""
converter.py — D097: Format-conversion tool.

Igor calls convert_text(text, from_format, to_format) to convert between:
  EN  — English prose
  CSB — pipe-delimited inline key:value (Compressed Structured Block)
  DSB — Distilled Structured Block document (DOC header + SECTION_* layout)

How it works:
  1. Look up conversion node in lists.conv: list_get('lists.conv', 'EN_CSB')
  2. Extract prompt template from narrative (between PROMPT_START and PROMPT_END)
  3. Substitute {text} with the input
  4. For long inputs: chunk and convert each chunk, then recombine
  5. Call LLM (gpt-4o-mini via OpenRouter) and return result

The prompt templates live in PROCEDURAL memories — not in this code.
This file is a thin wrapper: find template → call LLM.
"""

import json
import math
import os
import urllib.request
from typing import Optional

from devices.igor.tools.registry import Tool, registry

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/akienm/TheIgors"
# Read env directly to avoid circular import (tools → inference_openrouter → tools)
_DEFAULT_MODEL = os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")

_SUPPORTED = {"EN", "CSB", "DSB"}
_DEFAULT_CHUNK = 3000  # chars; ~750 tokens, safe margin for gpt-4o-mini 128k


# ── Core conversion call ──────────────────────────────────────────────────────


def _llm_convert(
    prompt: str, model: str = _DEFAULT_MODEL, max_tokens: int = 2000
) -> str:
    """Single LLM call for conversion. Returns converted text or error string."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return "ERROR: OPENROUTER_API_KEY not set"

    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a precise format converter. "
                        "Output ONLY the converted text — no preamble, no explanation, no markdown fences. "
                        "Follow the format rules exactly as given."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
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
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"ERROR: LLM call failed: {e}"


def _extract_template(narrative: str) -> Optional[str]:
    """Extract the prompt template between PROMPT_START and PROMPT_END markers."""
    start = narrative.find("PROMPT_START")
    end = narrative.find("PROMPT_END")
    if start == -1 or end == -1:
        return None
    return narrative[start + len("PROMPT_START") : end].strip()


def _get_conversion_memory(from_format: str, to_format: str, cortex):
    """Look up the CONV:* memory for this pair via lists.conv."""
    key = f"{from_format}_{to_format}"
    row = cortex.list_get("lists.conv", key)
    if not row:
        return None
    mem_id = row["item_value"]
    return cortex.get(mem_id)


def _chunk_text(text: str, chunk_size: int) -> list[str]:
    """Split text into chunks at paragraph or sentence boundaries near chunk_size."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > chunk_size:
        # Try to break at paragraph boundary
        split_at = remaining.rfind("\n\n", 0, chunk_size)
        if split_at == -1:
            # Fall back to newline
            split_at = remaining.rfind("\n", 0, chunk_size)
        if split_at == -1:
            # Fall back to space
            split_at = remaining.rfind(" ", 0, chunk_size)
        if split_at == -1:
            split_at = chunk_size
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)
    return chunks


# ── Public function ───────────────────────────────────────────────────────────


def convert_text_fn(
    text: str,
    from_format: str,
    to_format: str,
    chunk_size: int = _DEFAULT_CHUNK,
    cortex=None,
    **_,
) -> str:
    """
    Convert text between EN, CSB, and DSB formats.
    Uses the CONV:* PROCEDURAL memory prompt templates.
    Long inputs are chunked and results recombined.
    """
    from_format = from_format.upper().strip()
    to_format = to_format.upper().strip()

    if from_format not in _SUPPORTED or to_format not in _SUPPORTED:
        return (
            f"ERROR: unsupported format(s). Supported: {', '.join(sorted(_SUPPORTED))}"
        )

    if from_format == to_format:
        return text  # no-op

    # Get cortex from running Igor instance if not provided
    if cortex is None:
        try:
            from ..main import _running_instance

            if _running_instance is not None:
                cortex = _running_instance.cortex
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in devices/igor/tools/converter.py: %s", _bare_e
            )

    if cortex is None:
        return "ERROR: cortex not available — cannot look up conversion template"

    mem = _get_conversion_memory(from_format, to_format, cortex)
    if mem is None:
        return (
            f"ERROR: no conversion memory found for {from_format}→{to_format}. "
            "Run claudecode/seed_conv_graph.py to seed the conversion graph."
        )

    template = _extract_template(mem.narrative)
    if template is None:
        return f"ERROR: CONV:{from_format}_TO_{to_format} memory has no PROMPT_START/PROMPT_END template."

    chunks = _chunk_text(text, chunk_size)
    results = []

    for i, chunk in enumerate(chunks):
        prompt = template.replace("{text}", chunk)
        result = _llm_convert(prompt)
        if result.startswith("ERROR:"):
            return result
        results.append(result)

    if len(results) == 1:
        return results[0]

    # Recombine: join with double newline for EN/DSB, single newline for CSB
    separator = "\n" if to_format == "CSB" else "\n\n"
    combined = separator.join(results)

    if len(chunks) > 1:
        combined = f"// converted in {len(chunks)} chunks\n{combined}"

    return combined


# ── Igor tool registration ────────────────────────────────────────────────────


def _tool_convert_text(
    text: str,
    from_format: str,
    to_format: str,
    chunk_size: int = _DEFAULT_CHUNK,
    **_,
) -> str:
    return convert_text_fn(
        text=text,
        from_format=from_format,
        to_format=to_format,
        chunk_size=chunk_size,
    )


registry.register(
    Tool(
        name="convert_text",
        description=(
            "Convert text between formats: EN (English prose), CSB (pipe-delimited inline key:value), "
            "and DSB (Distilled Structured Block document). "
            "Uses CONV:* PROCEDURAL memory templates — run seed_conv_graph.py first. "
            "Long inputs are automatically chunked. "
            "Use when Akien asks to convert a document, or when preparing data for another system."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to convert.",
                },
                "from_format": {
                    "type": "string",
                    "enum": ["EN", "CSB", "DSB"],
                    "description": "Source format: EN, CSB, or DSB.",
                },
                "to_format": {
                    "type": "string",
                    "enum": ["EN", "CSB", "DSB"],
                    "description": "Target format: EN, CSB, or DSB.",
                },
                "chunk_size": {
                    "type": "integer",
                    "description": "Max chars per chunk for long inputs. Default 3000.",
                },
            },
            "required": ["text", "from_format", "to_format"],
        },
        fn=_tool_convert_text,
    )
)

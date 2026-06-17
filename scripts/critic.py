#!/usr/bin/env python3
"""
critic.py — Adversarial analysis CLI.

Usage:
    python scripts/critic.py <target> [--refresh] [--output json|text|markdown]

Target types (auto-detected):
    symbol   — a Python name (function/class), searched by grep
    module   — a file path (relative to repo root or absolute)
    ticket   — a ticket ID matching T-<slug>
    free     — anything else (used verbatim as context)

Output (default: text):
    json       — raw JSON matching the critic schema
    text       — human-readable bullet list
    markdown   — fenced markdown suitable for pasting into docs

Cache: ~/.unseen_university/critic_cache/ (TTL 1 hour, bypass with --refresh).

Requires: ANTHROPIC_API_KEY or UU_ANTHROPIC_API_KEY in environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure lab/claudecode is importable
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO / "lab" / "claudecode"))

from critic_core import (  # noqa: E402
    build_prompt,
    cache_get,
    cache_put,
    detect_target_type,
    fetch_context,
    CRITIC_SYSTEM,
)


def _call_anthropic(prompt: str) -> str:
    """Call Anthropic API with the critic prompt. Returns raw response text."""
    api_key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("UU_ANTHROPIC_API_KEY")
    )
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic package not installed (pip install anthropic)", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=CRITIC_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _parse_response(raw: str, target: str, target_type: str, context_summary: str) -> dict:
    """Parse LLM JSON response; fill in fields if the model omitted them."""
    raw = raw.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"WARNING: LLM returned non-JSON; wrapping as free-text: {exc}", file=sys.stderr)
        data = {
            "assumptions": [],
            "gaps": [],
            "risks": [raw],
            "suggestions": [],
            "confidence_level": "low",
        }
    data.setdefault("target", target)
    data.setdefault("target_type", target_type)
    data.setdefault("context_summary", context_summary)
    return data


def _format_text(result: dict) -> str:
    lines = [f"Critic report for: {result.get('target', '?')} ({result.get('target_type', '?')})",
             f"Context: {result.get('context_summary', '')}",
             f"Confidence: {result.get('confidence_level', '?')}",
             ""]
    for section, label in [
        ("assumptions", "Questionable assumptions"),
        ("gaps", "Gaps"),
        ("risks", "Risks"),
        ("suggestions", "Suggestions"),
    ]:
        items = result.get(section, [])
        if items:
            lines.append(f"{label}:")
            for item in items:
                lines.append(f"  - {item}")
            lines.append("")
    return "\n".join(lines)


def _format_markdown(result: dict) -> str:
    lines = [f"## Critic: `{result.get('target', '?')}`",
             f"*{result.get('context_summary', '')}* · confidence: {result.get('confidence_level', '?')}",
             ""]
    for section, label in [
        ("assumptions", "Questionable Assumptions"),
        ("gaps", "Gaps"),
        ("risks", "Risks"),
        ("suggestions", "Suggestions"),
    ]:
        items = result.get(section, [])
        if items:
            lines.append(f"### {label}")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="Target to critique: symbol, module path, ticket ID, or free text")
    parser.add_argument("--refresh", action="store_true", help="Bypass cache and re-run")
    parser.add_argument("--output", choices=["json", "text", "markdown"], default="text",
                        help="Output format (default: text)")
    args = parser.parse_args()

    target = args.target.strip()
    target_type = detect_target_type(target)

    # Cache check
    if not args.refresh:
        cached = cache_get(target)
        if cached:
            print(f"(cached)", file=sys.stderr)
            _print_result(cached, args.output)
            return

    # Fetch context
    context, context_summary = fetch_context(target, target_type)

    # Build prompt and call LLM
    prompt = build_prompt(target, target_type, context)
    print(f"Critiquing {target_type}: {target}", file=sys.stderr)
    raw = _call_anthropic(prompt)

    # Parse and cache
    result = _parse_response(raw, target, target_type, context_summary)
    cache_put(target, result)

    _print_result(result, args.output)


def _print_result(result: dict, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(result, indent=2))
    elif fmt == "markdown":
        print(_format_markdown(result))
    else:
        print(_format_text(result))


if __name__ == "__main__":
    main()

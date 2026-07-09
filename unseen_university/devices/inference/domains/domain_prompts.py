"""
domain_prompts.py — the system prompt as DATA, keyed by task DOMAIN.

Intention-Based Development declares the router routes BOTH model AND prompt by
domain: the system prompt is a property of the KIND of task (its domain), not
baked into the caller. So adding a domain's prompt is a DATA edit — drop a
`prompts/<domain>.md` file — with no change to any caller or selector code.

The prompt text lives in `prompts/<domain>.md` (one file per domain, byte-exact,
readable as prose — a prompt is data, not code). `domain_prompt(domain)` loads and
caches it. Unknown/'' domains resolve to '' so a caller falls back to its own
default; a populated domain resolves to its data-defined prompt.

Per-model prompt adapters (a prompt that varies by the chosen model within a
domain) are a FUTURE optional refinement — out of scope here; today the prompt is
keyed on domain alone.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# domain → resolved prompt text. Populated lazily on first request per domain so a
# new prompts/<domain>.md is picked up without a code change.
_CACHE: dict[str, str] = {}


def domain_prompt(domain: str, table: dict[str, str] | None = None) -> str:
    """Return the system prompt for a task `domain` ('' for unknown/generalist).

    Resolution is DATA-driven: the text is read from `prompts/<domain>.md`. An
    unknown domain (or '') returns '' — the caller keeps its own default. `table`
    injects an explicit domain→text map (test seam / future in-memory overrides),
    bypassing the file store entirely.
    """
    if table is not None:
        return table.get(domain, "")
    if not domain:
        return ""
    if domain in _CACHE:
        return _CACHE[domain]
    path = _PROMPTS_DIR / f"{domain}.md"
    if not path.exists():
        log.debug("domain_prompt: no prompt file for domain=%r (%s)", domain, path)
        return ""
    text = path.read_text(encoding="utf-8")
    _CACHE[domain] = text
    log.info("domain_prompt: loaded %r prompt (%d chars) from %s", domain, len(text), path.name)
    return text

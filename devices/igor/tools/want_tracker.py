import logging

"""
want_tracker.py — post-response want/request expression detector.

Fires as a daemon thread after each turn (gate: IGOR_WANT_TRACK, default true).
Detects when Igor has expressed a want or request to Akien in his response,
and deposits an EPISODIC memory so Igor remembers what he asked for.

Why: Igor can forget requests made across sessions, surprising Akien when
he delivers. This closes the loop: Igor knows what he asked, when, and why.
"""

import datetime
import json
import os
import re
import threading
import urllib.request
import uuid

# ── Want expression patterns (tier.1 — free scan before any API call) ─────────
# Deliberately permissive — false positives are cheap (extractor discards them).
_WANT_PATTERNS = [
    r"\b(could you|can you|would you|please)\b.{0,60}\b(get|find|buy|acquire|order|pick up|fetch|download|install|set up|configure|add|create|write|send|bring)\b",
    r"\bi('d| would) (like|love|appreciate) (you|if you|akien)\b",
    r"\bi('m| am) (asking|requesting|hoping you|wondering if you)\b",
    r"\b(would it be possible|is it possible) for you\b",
    r"\bi need you to\b",
    r"\bif you (get|find|buy|acquire|could get|can get|pick up)\b",
]

_WANT_RE = re.compile("|".join(_WANT_PATTERNS), re.IGNORECASE)

_EXTRACT_PROMPT = """\
Igor (an AI assistant) just sent this response to Akien (his human partner):

---
{response_text}
---

Does this response express a want, request, or ask directed at Akien — something Igor is asking Akien to do, get, find, buy, or arrange?

If YES, reply with JSON only (no markdown):
{{"want": "brief summary of what Igor asked for (1-2 sentences)", "motivation": "why Igor wants this (if stated, else empty string)", "timeframe": "when (if mentioned, else empty string)"}}

If NO want/request is expressed, reply with exactly: SKIP
"""


def _want_extract_worker(response_text: str, user_input: str, cortex) -> None:
    """Fire-and-forget daemon: extract want and deposit EPISODIC memory."""
    try:
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            return

        from ..cognition.inference_openrouter import OR_CHEAP_MODEL

        cheap_model = OR_CHEAP_MODEL
        prompt = _EXTRACT_PROMPT.format(response_text=response_text[:800])

        payload = json.dumps(
            {
                "model": cheap_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 120,
            }
        ).encode()

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/akienm/TheIgors",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        result = data["choices"][0]["message"]["content"].strip()

        if result.upper().startswith("SKIP") or not result.startswith("{"):
            return

        extracted = json.loads(result)
        want = extracted.get("want", "").strip()
        motivation = extracted.get("motivation", "").strip()
        timeframe = extracted.get("timeframe", "").strip()

        if not want:
            return

        from ..memory.models import Memory, MemoryType

        today = datetime.date.today().isoformat()
        narrative = f"Igor asked Akien on {today}: {want}"
        if motivation:
            narrative += f" Motivation: {motivation}"
        if timeframe:
            narrative += f" Timeframe: {timeframe}"

        mem_id = f"WANT_{str(uuid.uuid4())[:6].upper()}"
        mem = Memory(
            id=mem_id,
            narrative=narrative,
            memory_type=MemoryType.EPISODIC,
            source="want_tracker",
            confidence=0.85,
            context_of_encoding="want_tracker|post_response",
            portable=False,
            metadata={
                "want_summary": want,
                "motivation": motivation,
                "timeframe": timeframe,
                "trigger_context": user_input[:120],
            },
        )
        cortex.store(mem)

        try:
            from rich.console import Console as _C

            _C().print(f"[dim cyan][WANT] Recorded: {mem_id} — {want[:60]}[/]")
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/tools/want_tracker.py: %s", _bare_e
            )

    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/tools/want_tracker.py: %s", _bare_e
        )


def check_response_for_wants(response_text: str, cortex, user_input: str = "") -> None:
    """
    Scan Igor's response for want/request expressions.
    If found, fire a daemon thread to extract and deposit EPISODIC memory.
    Gate: IGOR_WANT_TRACK (default true).
    """
    if os.getenv("IGOR_WANT_TRACK", "true").lower() in ("0", "false", "no"):
        return
    if not response_text:
        return
    if not _WANT_RE.search(response_text):
        return  # Free exit — no pattern match, no cost

    t = threading.Thread(
        target=_want_extract_worker,
        args=(response_text, user_input, cortex),
        daemon=True,
        name="want-tracker",
    )
    t.start()

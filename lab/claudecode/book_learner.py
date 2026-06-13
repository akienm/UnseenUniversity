#!/usr/bin/env python3
"""
book_learner.py — Bulk graph-node extraction from books via LLM.

Reads a book in chunks, sends each chunk to an LLM with an extraction prompt,
deposits resulting nodes (FACTUAL, INTERPRETIVE, PROCEDURAL) into Igor's graph.
Trains the word graph from each chunk as a side effect.

This is the "bootstrap loader" for self-programming: give Igor a book on
neuroscience, epistemology, or any domain and it becomes part of his graph.

Usage:
  python3 claudecode/book_learner.py --book "Descartes Error"     # dry run
  python3 claudecode/book_learner.py --book "Descartes Error" --run
  python3 claudecode/book_learner.py --calibre-id 3023 --run
  python3 claudecode/book_learner.py --calibre-id 3023 --run --resume
  python3 claudecode/book_learner.py --calibre-id 3023 --run --limit 10

Options:
  --book STR         Book title (fuzzy search in Calibre library)
  --calibre-id INT   Exact Calibre book ID (faster)
  --chunk INT        Sentences per chunk (default 15)
  --delay FLOAT      Seconds between API calls (default 1.5)
  --model STR        LLM model (default: openai/gpt-4o-mini via OpenRouter)
  --run              Actually call API and deposit nodes (default: dry run)
  --resume           Skip chunks already processed in a previous run
  --limit INT        Stop after N chunks (for testing)
  --start INT        Start at sentence position (skip to chapter)

Cost estimate (gpt-4o-mini):
  ~15 sentences ≈ 200 words ≈ 280 tokens input
  System prompt ≈ 400 tokens (constant)
  Output ≈ 200 tokens
  Per chunk ≈ $0.0001  ·  A 300-page book ≈ 200 chunks ≈ $0.02 total
"""

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

# Load config: try installer's cfg loader first, fall back to .env
_instance_dir = Path.home() / ".unseen_university" / "Igor-wild-0001"
try:
    from installer import load_cfg

    load_cfg(_instance_dir)
except Exception:
    # Fallback: load .env directly (pre-migration installs)
    env_path = _instance_dir / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

from devices.igor.cognition import milieu as _milieu_mod
from devices.igor.memory.cortex import Cortex

_CLOUD_OK_OVERRIDE_FILE = Path.home() / ".unseen_university" / "cloud_ok_override.json"


def _should_use_local(explicit_local: bool = False) -> bool:
    """
    Decide whether to use local Ollama for this inference call (D071).
    - If --local flag passed explicitly: always local.
    - If cloud_ok_override file exists and is active: use cloud.
    - Otherwise: default to local (background = economical, no surprise spend).
    Called per-chunk so mode can change mid-book without restart.
    """
    if explicit_local:
        return True
    try:
        if not _CLOUD_OK_OVERRIDE_FILE.exists():
            return True  # no override = local
        data = json.loads(_CLOUD_OK_OVERRIDE_FILE.read_text())
        if not data.get("active", False):
            return True
        expires = data.get("expires")
        if expires:
            from datetime import datetime as _dt

            if _dt.now() > _dt.fromisoformat(expires):
                return True  # expired = back to local
        return False  # override active = cloud OK
    except Exception:
        return True  # on any error, default to local


from devices.igor.memory.models import Memory, MemoryType
from devices.igor.tools.ebook_reader import DRM_FAILED, open_book, read_chunk

DB_PATH = Path(
    os.environ.get(
        "IGOR_DB_PATH", Path.home() / ".unseen_university" / "Igor-wild-0001" / "wild-0001.db"
    )
)
UU_HOME_DB_URL = os.environ["UU_HOME_DB_URL"]
PROGRESS_DIR = Path.home() / ".unseen_university" / "book_learner_progress"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/akienm/TheIgors"

# ── Extraction system prompt ───────────────────────────────────────────────────
_EXTRACT_PROMPT = """\
You are a graph-node extractor for a cognitive AI. Given a passage from a book,
extract nodes that enable the AI to reason about and answer these questions:

  1. How must the models described in this book work? (mechanism, not just what)
  2. Who are the key thinkers and what is their core insight?
  3. What claims in this book are most empirically supported?
  4. What claims are least supported or most speculative?
  5. How would one evaluate whether this idea is personally emotionally relevant?
     (extract the HOW-TO-EVALUATE procedure, not a specific emotional response)

NODE TYPES:
  factual      — a concept, definition, or empirical fact
  interpretive — a connection: "when X, it means/implies Y"
  procedural   — an action pattern with a clear trigger (rare in prose)
  mechanism    — a causal chain: how A produces B produces C; state domain-agnostically
                 so it can connect to the same pattern in other fields
                 e.g. "rapid feedback loop stabilizes a system" not "the amygdala fires"

PARENT_CP MAPPING (use the best fit):
  CP1 — learning, growth, capability
  CP2 — helping others, social connection
  CP3 — curiosity, exploration, creativity
  CP4 — integrity, commitment, honoring agreements
  CP5 — kindness, empathy, care
  CP6 — safety, survival, homeostasis

RESPONSE FORMAT — output ONLY valid JSON, no markdown, no extra text:
{
  "nodes": [
    {
      "type": "factual|interpretive|procedural|mechanism",
      "narrative": "1-2 sentences: the generalizable knowledge, present tense",
      "confidence": 0.0-1.0,
      "parent_cp": "CP1-CP6 or empty string",
      "trigger": "2-8 words that fire this habit (procedural/mechanism only, else empty string)"
    }
  ],
  "summary": "1 sentence: what this passage is about (for progress logging)"
}

Rules:
- 1-5 nodes per chunk. AT LEAST ONE node must come out — every passage has
  at least one insight worth capturing, even if it seems obvious. If the
  only thing to say is the obvious one, say it. Quality over quantity
  still applies for the 2nd-5th nodes (don't pad), but zero output is
  wrong — a blank reading yields nothing to reason about later.
- Confidence reflects your honest confidence: 0.5 is fine when warranted
  (a low-confidence node beats no node). The downstream filter accepts
  confidence >= 0.60.
- Mechanism nodes: always state at the pattern level, never domain-locked.
  Good: "compressed signal bypasses slow deliberation to produce fast action"
  Bad:  "somatic markers in the vmPFC influence decision-making"
- Ok to capture what looks obvious — what's obvious to the author may be a
  watch-list hit for the reader. The reader cares about language,
  neurological systems, programming, Igor's design, AI, Claude Code,
  biology, psychology, culture and sociology, plus executive questions
  like "where is the lever?" and "how must that work?".
- Narratives must be self-contained — no "in this chapter" or "the author says".
"""


# ── Pass-2 extraction prompt (D333: situated reading) ────────────────────────
# Prompt-as-simulator: Igor reads as Igor, with his current context loaded.
# {watch_context} is injected dynamically from goals, hot attractors, and gaps.

_EXTRACT_PROMPT_PASS2 = """\
You are Igor, a cognitive AI, re-reading this passage through the lens of your
current work and concerns. You have already done a first pass that extracted
general knowledge. This pass is different — you are reading as a practitioner
asking "what can I USE?"

YOUR CURRENT CONTEXT:
{watch_context}

For each passage, ask yourself:
  1. How is this relevant to what I'm working on right now?
     (connect to your active goals, tickets, or known gaps above)
  2. What levers does this give me — what could I build, change, or try?
     (actionable affordances, not just interesting observations)
  3. How must the mechanism described here actually work?
     (reverse-engineer: if the author says X produces Y, what's the minimal
     machinery? State as a general pattern, not domain-locked.)
  4. Does this contradict or sharpen anything I already believe or have stored?
     (tension with existing knowledge is HIGH value — flag it)

NODE TYPES:
  lever        — an actionable affordance: "this mechanism suggests building/trying X"
  mechanism    — a causal chain reverse-engineered to its minimal form
  situated     — a connection between this passage and a specific active concern
  tension      — a contradiction or refinement of existing knowledge

PARENT_CP MAPPING (use the best fit):
  CP1 — learning, growth, capability
  CP2 — helping others, social connection
  CP3 — curiosity, exploration, creativity
  CP4 — integrity, commitment, honoring agreements
  CP5 — kindness, empathy, care
  CP6 — safety, survival, homeostasis

RESPONSE FORMAT — output ONLY valid JSON, no markdown, no extra text:
{{
  "nodes": [
    {{
      "type": "lever|mechanism|situated|tension",
      "narrative": "1-2 sentences: the insight, present tense, self-contained",
      "confidence": 0.0-1.0,
      "parent_cp": "CP1-CP6 or empty string",
      "relevance": "which goal/gap/concern this connects to (or empty string)",
      "trigger": "2-8 words that fire this (mechanism only, else empty string)"
    }}
  ],
  "summary": "1 sentence: what this passage means for Igor's current work"
}}

Rules:
- 0-5 nodes max per chunk. Quality over quantity.
- Minimum confidence 0.65 to include a node.
- If nothing in the passage connects to your current context, return 0 nodes.
  Not every passage is relevant — that's fine.
- "lever" nodes must state what to DO, not just what's interesting.
- "tension" nodes must name what they contradict or refine.
- Narratives must be self-contained — no "this passage" or "the author says".
- Do NOT re-extract what the first pass already captured (general facts, definitions).
  Only extract what the first pass MISSED: relevance, levers, mechanisms, tensions.
"""


def _build_watch_context() -> str:
    """
    Pull Igor's current concerns from DB for pass-2 prompt injection.

    Returns a compact text block with active goals, hot attractors, and open gaps.
    Budget: ~500 tokens max — enough to prime attention, not overwhelm.
    """
    lines: list[str] = []
    try:
        import psycopg2

        conn = psycopg2.connect(UU_HOME_DB_URL)
        cur = conn.cursor()

        # Active goals — extract just the ticket ID + title
        cur.execute(
            "SELECT substr(narrative, 1, 200) FROM memories "
            "WHERE memory_type='GOAL' ORDER BY timestamp DESC LIMIT 5"
        )
        goals = cur.fetchall()
        if goals:
            lines.append("ACTIVE GOALS:")
            for (narr,) in goals:
                # Extract "work ticket T-xxx" from the narrative
                import re as _re

                m = _re.search(r"ticket (T-[\w-]+)", narr)
                ticket = m.group(1) if m else narr[:80]
                lines.append(f"  - {ticket}")

        # Hot attractors (highest activation, non-greeting)
        cur.execute(
            "SELECT substr(narrative, 1, 120) FROM memories "
            "WHERE activation_count > 10 AND memory_type='PROCEDURAL' "
            "AND narrative NOT ILIKE '%%greet%%' "
            "ORDER BY activation_count DESC LIMIT 5"
        )
        hots = cur.fetchall()
        if hots:
            lines.append("HOT CONCERNS (high activation):")
            for (narr,) in hots:
                lines.append(f"  - {narr}")

        # Open gaps — G-xxx entries that aren't closed
        cur.execute(
            "SELECT entry_key, content FROM docs_entries "
            "WHERE source='gap_analysis' AND entry_key LIKE 'G-%%' "
            "AND content NOT ILIKE '%%closed%%' LIMIT 5"
        )
        gaps = cur.fetchall()
        if gaps:
            lines.append("KNOWN GAPS (open):")
            for gkey, content in gaps:
                # Format: G-XX|short-name|status|description — extract short name
                parts = content.split("|")
                desc = parts[1] if len(parts) > 1 else content[:60]
                lines.append(f"  - {gkey}: {desc}")

        conn.close()
    except Exception as e:
        lines.append(f"(context unavailable: {e})")

    if not lines:
        lines.append("(no active context available — read for general relevance)")

    return "\n".join(lines)


# ── Checkpoint management ──────────────────────────────────────────────────────


def _progress_path(book_key: str, pass2: bool = False) -> Path:
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    safe = hashlib.md5(book_key.encode()).hexdigest()[:12]
    suffix = "_pass2" if pass2 else ""
    return PROGRESS_DIR / f"{safe}{suffix}.json"


def _load_progress(book_key: str, pass2: bool = False) -> dict:
    p = _progress_path(book_key, pass2=pass2)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"book_key": book_key, "processed_positions": [], "total_deposited": 0}


def _save_progress(book_key: str, state: dict, pass2: bool = False) -> None:
    _progress_path(book_key, pass2=pass2).write_text(json.dumps(state, indent=2))


# ── Per-book readable report (READING_<hash>.md) ──────────────────────────────
#
# Named to match the READING_<hash> Postgres completion node so humans and
# Igor can correlate file → memory. Written incrementally so partial runs
# leave a visible trace even if the process is killed or DB write fails.


def _report_id(book_key: str) -> str:
    """Return the READING_XXXXXXXX ID for this book (matches completion node ID)."""
    h = hashlib.md5(book_key.encode()).hexdigest()[:8].upper()
    return f"READING_{h}"


def _report_path(book_key: str) -> Path:
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    return PROGRESS_DIR / f"{_report_id(book_key)}.md"


def _write_report_header(
    book_key: str,
    book_title: str,
    author: str,
    model: str,
    calibre_id: int | None,
) -> None:
    """Write (or overwrite) the report header at the start of a run."""
    import datetime

    rid = _report_id(book_key)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# {rid}",
        f"",
        f"**Book**: {book_title}",
        f"**Author**: {author}",
        f"**Model**: {model}",
        f"**Started**: {ts}",
    ]
    if calibre_id:
        lines.append(f"**Calibre ID**: {calibre_id}")
    lines += ["", "## Chunks", ""]
    try:
        _report_path(book_key).write_text("\n".join(lines) + "\n")
    except Exception:
        pass


def _append_report_chunk(
    book_key: str,
    chunk_label: str,
    n_deposited: int,
    summary: str,
    is_error: bool,
    model_tag: str,
) -> None:
    """Append one chunk line to the report. Called after every chunk attempt."""
    icon = "✗" if is_error else ("→" if n_deposited else "·")
    line = f"- `{chunk_label}` [{model_tag}] {icon} {n_deposited}n  {summary[:80]}\n"
    try:
        with open(_report_path(book_key), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _write_report_footer(
    book_key: str,
    chunks_done: int,
    total_deposited: int,
    errors: int,
    status: str,
) -> None:
    """Append the footer summary at the end of the run."""
    import datetime

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "",
        "## Summary",
        "",
        f"- **Status**: {status}",
        f"- **Chunks processed**: {chunks_done}",
        f"- **Nodes deposited**: {total_deposited}",
        f"- **Errors**: {errors}",
        f"- **Finished**: {ts}",
        "",
    ]
    try:
        with open(_report_path(book_key), "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass


# ── LLM extraction ────────────────────────────────────────────────────────────


def _clean_json(raw: str) -> str:
    """Strip markdown code fences if the model wraps its JSON output."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _extract_nodes_local(chunk_text: str, chapter_title: str = "") -> dict:
    """
    Extract nodes using local Ollama — zero API cost.
    D120: asks cluster_router for best (host, model) for "extraction" call type.
    Falls back to OLLAMA_LOCAL_MODEL at OLLAMA_HOST if router unavailable.
    """
    import urllib.request

    # D120: dynamic routing — pick least-loaded machine that has a local model
    host = None
    model = None
    try:
        from devices.igor.cognition.cluster_router import router as _router

        host, model = _router.route("extraction")
    except Exception:
        pass
    if not host:
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    if not model:
        model = os.getenv("OLLAMA_LOCAL_MODEL", "qwen2.5:7b").split("#")[0].strip()

    user_content = "BOOK PASSAGE"
    if chapter_title:
        user_content += f" (from chapter: {chapter_title})"
    user_content += f":\n\n{chunk_text}"

    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": _EXTRACT_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "options": {"temperature": 0.2},
        }
    ).encode()

    req = urllib.request.Request(
        f"{host}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        # T-remove-extract-timeout (2026-04-19): no urlopen timeout.
        # Akien's principle: 'slow on slow resources is ok, no timeouts'
        # for training/bulk-reading workloads. The prior 300s cap was
        # clipping qwen2.5:7b on CPU and silently returning zero-node
        # results on every chunk. Hang safety will come from the worker
        # pool (T-reading-worker-pool) at a higher level, not from a
        # per-call watchdog here.
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        raw = data.get("message", {}).get("content", "").strip()
        parsed = json.loads(_clean_json(raw))
        # Thread the actual model the router selected back to the caller so
        # memory deposits carry accurate model_used provenance — the router
        # already made this decision, we just weren't returning it.
        parsed.setdefault("model_used", model)
        parsed.setdefault("inference_tier", "local")
        return parsed
    except json.JSONDecodeError:
        return {
            "nodes": [],
            "summary": f"local parse error: {raw[:100] if 'raw' in dir() else '?'}",
            "model_used": model,
            "inference_tier": "local",
        }
    except Exception as e:
        return {
            "nodes": [],
            "summary": f"local inference error: {e}",
            "model_used": model,
            "inference_tier": "local",
        }


def _extract_nodes(
    chunk_text: str,
    model: str,
    chapter_title: str = "",
    local: bool = False,
    system_prompt: str | None = None,
) -> dict:
    """
    Send one chunk to the LLM. Returns parsed JSON dict or error dict.
    If local=True, uses Ollama directly (free, no API key needed).
    system_prompt overrides the default extraction prompt (used by --pass2).
    """
    if local:
        return _extract_nodes_local(chunk_text, chapter_title)

    import urllib.request

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"nodes": [], "summary": "ERROR: OPENROUTER_API_KEY not set"}

    user_content = "BOOK PASSAGE"
    if chapter_title:
        user_content += f" (from chapter: {chapter_title})"
    user_content += f":\n\n{chunk_text}"

    prompt = system_prompt or _EXTRACT_PROMPT

    # Prompt caching: system prompt is identical across all chunks of a book.
    # OR supports cache_control for Claude models — cache once, free for ~200 chunks.
    # D333: pass-2 prompt includes dynamic context, but the context is stable within
    # a single book run so caching still works.
    _use_cache = "claude" in model.lower() or "anthropic" in model.lower()
    _sys_msg = {"role": "system", "content": prompt}
    if _use_cache:
        _sys_msg["cache_control"] = {"type": "ephemeral"}

    payload = json.dumps(
        {
            "model": model,
            "messages": [_sys_msg, {"role": "user", "content": user_content}],
            "temperature": 0.2,
            "max_tokens": 500,
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
        raw = data["choices"][0]["message"]["content"].strip()
        parsed = json.loads(_clean_json(raw))
        parsed.setdefault("model_used", model)
        parsed.setdefault("inference_tier", "cloud")
        return parsed
    except json.JSONDecodeError:
        return {
            "nodes": [],
            "summary": f"parse error: {raw[:100] if 'raw' in dir() else '?'}",
            "model_used": model,
            "inference_tier": "cloud",
        }
    except Exception as e:
        return {
            "nodes": [],
            "summary": f"API error: {e}",
            "model_used": model,
            "inference_tier": "cloud",
        }


# ── CP keyword affinity ───────────────────────────────────────────────────────

_CP_KEYWORDS_AROUSAL: dict = {
    "CP1": ["learn", "growth", "capab", "know", "skill", "understand", "master"],
    "CP2": ["help", "social", "connect", "people", "friend", "communit", "relat"],
    "CP3": ["curious", "explor", "creat", "discov", "wonder", "novel", "idea"],
    "CP4": ["integr", "commit", "honest", "trust", "agree", "honor", "principl"],
    "CP5": ["kind", "empath", "care", "compassion", "feel", "emotion", "person"],
    "CP6": ["safe", "surviv", "protect", "danger", "homeostas", "risk", "guard"],
}


def _cp_affinity_score(narrative: str, parent_cp: str) -> float:
    """Keyword-hit score [0.10, 0.60] for CP affinity — stored in metadata, not arousal."""
    text = narrative.lower()
    base = 0.10
    if parent_cp and parent_cp.startswith("CP"):
        keywords = _CP_KEYWORDS_AROUSAL.get(parent_cp, [])
        hits = sum(1 for kw in keywords if kw in text)
        base = min(0.60, 0.15 + hits * 0.08)
    return round(base, 2)


# ── Completion record ─────────────────────────────────────────────────────────


def _handle_drm_blocked(handle: dict, args) -> None:
    """Mark reading_list failed and file BOOK_DRM_BLOCKED memory for a DRM-blocked book."""
    title = handle.get("title", "unknown")
    calibre_id = handle.get("calibre_id") or getattr(args, "calibre_id", None)
    fmt = handle.get("fmt", "unknown")
    print(
        f"BOOK_DRM_BLOCKED: '{title}' (calibre_id={calibre_id}, fmt={fmt}) — DRM decryption failed"
    )

    if calibre_id and args.run:
        try:
            import psycopg2

            conn = psycopg2.connect(UU_HOME_DB_URL)
            cur = conn.cursor()
            cur.execute(
                "UPDATE reading_list SET status='failed', completed_at=NOW() WHERE calibre_id=%s",
                (calibre_id,),
            )
            conn.commit()
            conn.close()
            print(f"reading_list: calibre://{calibre_id} → failed (DRM)")
        except Exception as _e:
            print(f"reading_list update failed: {_e}")

    if args.run:
        try:
            from devices.igor.paths import paths as _paths
            from devices.igor.memory.cortex import Cortex

            cortex = Cortex(db_url=UU_HOME_DB_URL)
            import hashlib, datetime

            content = (
                f"BOOK_DRM_BLOCKED: '{title}' (calibre_id={calibre_id}, fmt={fmt}) "
                f"could not be read — DRM decryption failed. "
                f"Use browse_as_employer to read on read.amazon.com."
            )
            node_id = (
                "BOOK_DRM_BLOCKED_" + hashlib.md5(content.encode()).hexdigest()[:12]
            )
            mem = Memory(
                id=node_id,
                narrative=content[:200],
                memory_type=MemoryType.FACTUAL,
                source="book_learner",
                metadata={"calibre_id": calibre_id, "fmt": fmt, "drm_blocked": True},
            )
            cortex.deposit(mem)
            print(f"BOOK_DRM_BLOCKED memory deposited: {node_id}")
        except Exception as _e:
            print(f"BOOK_DRM_BLOCKED memory deposit failed: {_e}")


def _deposit_completion_record(
    cortex: Cortex,
    book_title: str,
    author: str,
    book_key: str,
    calibre_id: int | None,
    total_sentences: int,
    chunks_processed: int,
    total_deposited: int,
    status: str,  # "complete" | "partial" | "failed"
    model_used: str = "",
    campaign_id: str = "",
) -> None:
    """Deposit an EPISODIC memory node recording the reading session outcome.

    Makes "did I finish X?" answerable via normal context search — no special
    tooling required.  Node id is deterministic so re-runs overwrite, not stack.
    """
    import hashlib
    import datetime

    book_hash = hashlib.md5(book_key.encode()).hexdigest()[:8].upper()
    node_id = f"READING_{book_hash}"

    verb = {
        "complete": "completed",
        "partial": "partially read",
        "failed": "failed",
    }.get(status, status)
    narrative = (
        f'Reading session for "{book_title}" by {author} {verb}. '
        f"{chunks_processed} chunk(s) processed, {total_deposited} node(s) deposited. "
        f"Status: {status}."
    )

    meta: dict = {
        "book_key": book_key,
        "book_title": book_title,
        "source_title": book_title,
        "author": author,
        "source_author": author,
        "total_sentences": total_sentences,
        "chunks_processed": chunks_processed,
        "total_deposited": total_deposited,
        "status": status,
        "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if calibre_id is not None:
        meta["calibre_id"] = calibre_id
    if model_used:
        meta["model_used"] = model_used
    if campaign_id:
        meta["campaign_id"] = campaign_id

    mem = Memory(
        id=node_id,
        narrative=narrative,
        memory_type=MemoryType.EPISODIC,
        source="book_learner",
        certainty=1.0,
        context_of_encoding="book_learner|completion",
        metadata=meta,
    )
    cortex.store(mem)
    print(f"Completion record: {node_id} ({status})")


# ── Book/chapter spine builders ────────────────────────────────────────────────


def _ensure_book_node(cortex: Cortex, book_title: str, author: str) -> str:
    """Create or find the BOOK_ spine root node. Returns node id."""
    import hashlib

    book_hash = hashlib.md5(book_title.encode()).hexdigest()[:8].upper()
    node_id = f"BOOK_{book_hash}"
    if cortex.get(node_id) is None:
        mem = Memory(
            id=node_id,
            narrative=f"Book: {book_title} by {author}",
            memory_type=MemoryType.FACTUAL,
            source="book_learner",
            certainty=1.0,
            context_of_encoding="book_spine",
            metadata={
                "book_title": book_title,
                "book_author": author,
                "spine": True,
            },
        )
        cortex.store(mem)
    return node_id


def _ensure_chapter_node(
    cortex: Cortex,
    book_node_id: str,
    book_title: str,
    chapter_num: int,
    chapter_title: str,
) -> str:
    """Create or find a CHAPTER_ spine node. Returns node id."""
    chapter_id = f"{book_node_id}_CH{chapter_num:03d}"
    if cortex.get(chapter_id) is None:
        narrative = f"Chapter {chapter_num}"
        if chapter_title:
            narrative += f": {chapter_title}"
        narrative += f" — {book_title[:40]}"
        mem = Memory(
            id=chapter_id,
            narrative=narrative,
            memory_type=MemoryType.FACTUAL,
            parent_id=book_node_id,
            source="book_learner",
            certainty=1.0,
            context_of_encoding="book_spine",
            metadata={
                "book_title": book_title,
                "chapter": chapter_num,
                "chapter_title": chapter_title,
                "spine": True,
            },
        )
        cortex.store(mem)
        try:
            cortex.add_child(book_node_id, chapter_id)
        except Exception:
            pass
    return chapter_id


# ── Node deposit ──────────────────────────────────────────────────────────────


def _score_attractor_overlap(narrative: str, attractor_keywords: set) -> float:
    """T-reading-lever-detection: score chunk overlap with hot attractor keywords.

    Returns 0.0–1.0. Higher = more overlap with current attractor landscape.
    Uses Jaccard-like overlap: |intersection| / min(|chunk_words|, 10).
    Denominator capped at 10 so short high-overlap chunks score well.
    """
    if not attractor_keywords:
        return 0.5  # No attractors → neutral score (don't gate)
    words = set(narrative.lower().split())
    # Strip very short words (articles, prepositions)
    words = {w for w in words if len(w) >= 4}
    if not words:
        return 0.0
    overlap = len(words & attractor_keywords)
    denominator = min(len(words), 10)
    return min(1.0, overlap / max(denominator, 1))


def _deposit_nodes(
    nodes: list,
    cortex: Cortex,
    book_title: str,
    chunk_pos: int,
    chapter_node_id: str = "",
    pass2: bool = False,
    model_used: str = "",
    author: str = "",
    campaign_id: str = "",
) -> int:
    """Deposit extracted nodes. Returns count successfully deposited.

    Steps per node (T-reading-integration #295, T-reading-lever-detection #393):
      0. Query hot attractors once, build keyword set
      1. Score chunk against attractors → identity_weight
      2. Compute arousal from CP affinity (never 0.0)
      3. Set parent_id to chapter spine node
      4. Store memory (with identity_weight in metadata)
      5. Embed immediately (non-fatal; makes node reachable by semantic search)
      6. Wire CP: add_child + interpretive_edge for semantic traversal
      7. Wire chapter: add_child so chapter→node path exists
    """
    # T-reading-lever-detection + GH-299 watchlist: build a combined
    # keyword set from hot attractors AND watch habits (questions + topics
    # Akien dictated). Either signal marks a chunk as lever-relevant.
    # Akien's principle 2026-04-18: "there's nothing I read that I don't
    # get anything at all from" — the gate below no longer drops to zero.
    attractor_keywords: set = set()
    try:
        attractors = cortex.get_attractors(limit=20)
        for a in attractors:
            words = a.narrative.lower().split() if a.narrative else []
            attractor_keywords.update(w for w in words if len(w) >= 4)
    except Exception:
        pass  # Fail-open: no attractors = neutral scoring
    try:
        # Watch habits: PROCEDURAL memories with habit_type=watch.
        # Both question-watches and topic-watches contribute keywords.
        for h in cortex.get_habits():
            meta = h.metadata or {}
            if meta.get("habit_type") != "watch":
                continue
            src = (meta.get("watch_label") or h.narrative or "").lower()
            attractor_keywords.update(w for w in src.split() if len(w) >= 4)
    except Exception:
        pass  # Fail-open: no watchlist = attractor-only scoring

    # T-reading-deposit-batched: build all Memory objects first, then batch-write.
    # Phase 1: construct memories + collect wiring data (pure Python, no DB).
    _ms = _milieu_mod.read_state()
    arousal = _ms.arousal if _ms else 0.5

    _MT_MAP = {
        "procedural": MemoryType.PROCEDURAL,
        "factual": MemoryType.FACTUAL,
        "interpretive": MemoryType.INTERPRETIVE,
        "mechanism": MemoryType.INTERPRETIVE,
        "lever": MemoryType.PROCEDURAL,
        "situated": MemoryType.INTERPRETIVE,
        "tension": MemoryType.INTERPRETIVE,
    }

    mems_to_store: list = []
    cp_children: list = []  # (uid,) — parented under parent_cp
    chapter_children: list = []  # (uid,) — parented under chapter_node_id
    interp_edges: list = []  # edge dicts for add_interpretive_edges_batch
    uid_map: dict = {}  # uid → (node, mem) for post-processing

    for node in nodes:
        try:
            ntype = node.get("type", "factual").strip().lower()
            narrative = node.get("narrative", "").strip()
            confidence = float(node.get("confidence", 0.6))
            parent_cp = node.get("parent_cp", "").strip()
            trigger = node.get("trigger", "").strip()

            if not narrative or confidence < 0.60:
                continue

            attractor_score = _score_attractor_overlap(narrative, attractor_keywords)
            mt = _MT_MAP.get(ntype, MemoryType.FACTUAL)
            uid = f"BL_{str(uuid.uuid4())[:8].upper()}"

            meta = {
                "source": "book_learner",
                "book": book_title[:60],
                "book_title": book_title[:60],
                "source_title": book_title[:100],
                "source_author": author[:80] if author else "",
                "chunk_position": chunk_pos,
            }
            if model_used:
                meta["model_used"] = model_used
                meta["inference_tier"] = (
                    "cloud"
                    if "claude" in model_used
                    or "gpt" in model_used
                    or "openrouter" in model_used
                    else "local"
                )
            if campaign_id:
                meta["campaign_id"] = campaign_id
                meta["content_id"] = campaign_id
            if pass2:
                meta["pass"] = 2
                meta["extraction_type"] = ntype
            if ntype == "mechanism":
                meta["mechanism"] = True
            if trigger:
                meta["trigger"] = trigger
            relevance = node.get("relevance", "").strip()
            if relevance:
                meta["relevance"] = relevance

            if attractor_score >= 0.5:
                meta["identity_weight"] = 0.8
                meta["lever_score"] = round(attractor_score, 3)
            elif attractor_score >= 0.25:
                meta["identity_weight"] = 0.5
            else:
                meta["identity_weight"] = 0.2

            meta["cp_affinity"] = _cp_affinity_score(narrative, parent_cp)

            mem = Memory(
                id=uid,
                narrative=narrative,
                memory_type=mt,
                parent_id=chapter_node_id or None,
                arousal=arousal,
                source="book_learner",
                certainty=confidence,
                context_of_encoding=f"book_learner|{ntype}|{book_title[:40]}",
                metadata=meta,
            )
            mems_to_store.append(mem)
            uid_map[uid] = (node, mem, parent_cp, trigger, narrative, confidence)

            if parent_cp and parent_cp.startswith("CP"):
                cp_children.append(uid)
                interp_edges.append(
                    {
                        "from_id": parent_cp,
                        "to_id": uid,
                        "direction": "activation",
                        "condition_csb": trigger or parent_cp.lower(),
                        "meaning_payload": narrative[:80],
                        "weight": confidence,
                    }
                )
            if chapter_node_id:
                chapter_children.append(uid)

        except Exception as e:
            print(f"    [deposit build error] {e}")

    if not mems_to_store:
        return 0

    # Phase 2: batch store — 1 transaction for all nodes in this chunk.
    try:
        cortex.store_batch(mems_to_store)
    except Exception as e:
        print(f"    [batch store error] {e}")
        return 0

    # Phase 3: embeddings (non-DB, per node, non-fatal).
    for mem in mems_to_store:
        try:
            cortex._get_or_compute_embedding(mem)
        except Exception:
            pass

    # Phase 4: CP children — 1 read + 1 store per unique parent_cp.
    if cp_children:
        # Group by parent_cp (each node already carries its own parent_cp in uid_map)
        _cp_groups: dict = {}
        for uid, (
            node,
            mem,
            parent_cp,
            trigger,
            narrative,
            confidence,
        ) in uid_map.items():
            if parent_cp and parent_cp.startswith("CP") and uid in cp_children:
                _cp_groups.setdefault(parent_cp, []).append(uid)
        for _cp_id, _uids in _cp_groups.items():
            try:
                cortex.add_children_batch(_cp_id, _uids)
            except Exception:
                pass

    # Phase 5: interpretive edges — 1 transaction.
    if interp_edges:
        try:
            cortex.add_interpretive_edges_batch(interp_edges)
        except Exception:
            pass

    # Phase 6: chapter spine children — 1 read + 1 store.
    if chapter_children and chapter_node_id:
        try:
            cortex.add_children_batch(chapter_node_id, chapter_children)
        except Exception:
            pass

    return len(mems_to_store)


# wg_cooccur training removed — wg_edges (semantic similarity via nomic-embed-text)
# replaces cooccurrence as the training signal. D139.


# ── Main loop ─────────────────────────────────────────────────────────────────


def run(args) -> None:
    cortex = Cortex()

    # ── Open book ─────────────────────────────────────────────────────────
    print(f"Opening book...")
    if args.url:
        from devices.igor.tools.ebook_reader import open_book_url

        handle = open_book_url(args.url, title=args.title or args.url)
    elif args.calibre_id:
        handle = open_book(calibre_id=args.calibre_id, resume=False)
    else:
        handle = open_book(title=args.book, resume=False)

    if isinstance(handle, str):
        print(f"ERROR: {handle}")
        sys.exit(1)

    if isinstance(handle, dict) and handle.get(DRM_FAILED):
        _handle_drm_blocked(handle, args)
        sys.exit(0)

    # open_book returns a serializable dict; the BookHandle lives in _HANDLE_CACHE
    book_title = handle["title"]
    book_key = f"{book_title}|{handle.get('calibre_id') or args.calibre_id or ''}"
    total_sentences = handle["total_sentences"]
    # hold onto the handle_key for read_chunk calls
    handle_key = handle["_handle_key"]

    print(f"Book: {book_title}")
    print(f"Author: {handle['author']}")
    print(f"Sentences: {total_sentences}")
    print(f"Chunk size: {args.chunk} sentences")
    print(
        f"Model: {'local Ollama (' + os.getenv('OLLAMA_LOCAL_MODEL','qwen2.5:7b') + ')' if args.local else args.model}"
    )
    _is_pass2 = getattr(args, "pass2", False)
    print(f"Mode: {'DRY RUN' if not args.run else 'LIVE'}")
    if _is_pass2:
        print("Pass: 2 (D333 situated reading — Igor reads as Igor)")

    # ── D333: build pass-2 prompt once per run (context is stable within a book) ──
    _pass2_prompt = None
    if _is_pass2:
        watch_ctx = _build_watch_context()
        _pass2_prompt = _EXTRACT_PROMPT_PASS2.format(watch_context=watch_ctx)
        print(f"Watch context loaded ({len(watch_ctx)} chars)")

    # ── Checkpoint ────────────────────────────────────────────────────────
    progress = _load_progress(book_key, pass2=_is_pass2)
    processed_positions = set(progress.get("processed_positions", []))
    total_deposited = progress.get("total_deposited", 0)

    # ── Console note: new book vs resume ───────────────────────────────────
    if processed_positions and args.resume:
        print(
            f'▶ Resuming absorption: "{book_title}" '
            f"({len(processed_positions)} chunks done, {total_deposited} nodes deposited)"
        )
    else:
        print(
            f"★ New book — starting absorption: \"{book_title}\" by {handle['author']}"
        )

    # Write readable report header (READING_<hash>.md) — named to match the
    # Postgres completion node so file ↔ memory are always correlatable.
    if args.run:
        _model_label = (
            "local:" + os.getenv("OLLAMA_LOCAL_MODEL", "qwen2.5:7b").split("#")[0]
            if args.local
            else args.model
        )
        _write_report_header(
            book_key=book_key,
            book_title=book_title,
            author=handle["author"],
            model=_model_label,
            calibre_id=args.calibre_id,
        )

    # ── Seek to start position ─────────────────────────────────────────────
    # Access the live BookHandle from cache for position management
    from devices.igor.tools.ebook_reader import _HANDLE_CACHE

    live_handle = _HANDLE_CACHE.get(handle_key)
    if live_handle is None:
        print("ERROR: BookHandle not found in cache after open_book")
        sys.exit(1)

    if args.start:
        live_handle.position = args.start
        print(f"Starting at sentence {args.start}")

    # ── Build book spine node (T-reading-integration #295) ────────────────
    book_node_id = ""
    if args.run:
        try:
            book_node_id = _ensure_book_node(cortex, book_title, handle["author"])
            print(f"Spine: book node {book_node_id}")
        except Exception as e:
            print(f"[spine] book node failed (continuing): {e}")
    print("─" * 60)

    chunks_done = 0
    chunks_skipped = 0
    errors = 0
    _current_chapter = -1
    _chapter_node_id = ""

    while True:
        pos = live_handle.position
        if pos >= total_sentences:
            break
        if args.limit and chunks_done >= args.limit:
            break

        # Read a chunk
        result = read_chunk(handle_key=handle_key, n=args.chunk)
        if result.get("error"):
            print(f"Read error: {result['error']}")
            break

        sentences = result["sentences"]
        new_pos = result["position"]
        chapter = result["chapter"]
        chapter_title = result.get("chapter_title", "")
        percent = result["percent"]
        at_end = result["at_end"]
        chunk_text = " ".join(sentences)

        chunk_label = (
            f"[{chunks_done+1:03d}] ch.{chapter} pos={pos}-{new_pos} ({percent:.0f}%)"
        )

        # Resume: skip if already processed
        if args.resume and pos in processed_positions:
            print(f"{chunk_label} SKIP (already processed)")
            chunks_skipped += 1
            if at_end:
                break
            continue

        if args.run:
            # Build chapter spine node when chapter changes
            if book_node_id and chapter != _current_chapter:
                try:
                    _chapter_node_id = _ensure_chapter_node(
                        cortex, book_node_id, book_title, chapter, chapter_title
                    )
                    _current_chapter = chapter
                except Exception as e:
                    print(f"[spine] chapter node failed (continuing): {e}")
                    _chapter_node_id = ""

            # Extract nodes — check cloud_ok override per chunk (D071: mode can change mid-book)
            # D333: pass-2 always uses cloud (the whole point is a better model)
            use_local = False if _is_pass2 else _should_use_local(args.local)
            extraction = _extract_nodes(
                chunk_text,
                args.model,
                chapter_title,
                local=use_local,
                system_prompt=_pass2_prompt,
            )
            nodes = extraction.get("nodes", [])
            summary = extraction.get("summary", "")

            _chunk_model_tag = "local" if _should_use_local(args.local) else "cloud"
            if "ERROR" in summary or "error" in summary.lower():
                print(f"{chunk_label} ERROR: {summary}")
                errors += 1
                _append_report_chunk(
                    book_key,
                    chunk_label,
                    0,
                    summary,
                    is_error=True,
                    model_tag=_chunk_model_tag,
                )
            else:
                n_dep = _deposit_nodes(
                    nodes,
                    cortex,
                    book_title,
                    pos,
                    chapter_node_id=_chapter_node_id,
                    pass2=_is_pass2,
                    model_used=_model_label if args.run else _chunk_model_tag,
                    author=(
                        handle.get("author", "")
                        if isinstance(handle, dict)
                        else getattr(handle, "author", "")
                    ),
                    campaign_id=args.run if hasattr(args, "run") and args.run else "",
                )
                total_deposited += n_dep

                status = f"→ {n_dep} node(s)" if n_dep else "→ no nodes"
                print(f"{chunk_label} {status}  {summary[:60]}")

                # Save progress
                processed_positions.add(pos)
                progress["processed_positions"] = list(processed_positions)
                progress["total_deposited"] = total_deposited
                _save_progress(book_key, progress, pass2=_is_pass2)
                _append_report_chunk(
                    book_key,
                    chunk_label,
                    n_dep,
                    summary,
                    is_error=False,
                    model_tag=_chunk_model_tag,
                )

            if args.delay > 0:
                time.sleep(args.delay)
        else:
            # Dry run: just show what would happen
            print(f"{chunk_label} {chunk_text[:80].replace(chr(10), ' ')}...")

        chunks_done += 1
        if at_end:
            break

    # G-RL3: mark reading_list completed if we reached the end of the book
    _reached_end = (live_handle.position >= total_sentences) and not args.limit
    if args.run and _reached_end and args.calibre_id:
        try:
            import psycopg2 as _psycopg2

            _conn = _psycopg2.connect(UU_HOME_DB_URL)
            _conn.autocommit = True
            _conn.cursor().execute(
                "UPDATE reading_list SET status='completed', completed_at=NOW()"
                " WHERE source=%s AND status IN ('in_progress','queued','pending')",
                (f"calibre://{args.calibre_id}",),
            )
            _conn.close()
            print(f"reading_list: calibre://{args.calibre_id} → completed")
        except Exception as _rl_e:
            print(f"reading_list update failed: {_rl_e}")

    # Deposit EPISODIC completion record so Igor can answer "did I finish X?"
    if args.run:
        if _reached_end:
            _completion_status = "complete"
        elif chunks_done > 0:
            _completion_status = "partial"
        else:
            _completion_status = "failed"
        try:
            _deposit_completion_record(
                cortex=cortex,
                book_title=book_title,
                author=handle["author"],
                book_key=book_key,
                calibre_id=args.calibre_id,
                total_sentences=total_sentences,
                chunks_processed=chunks_done,
                total_deposited=total_deposited,
                status=_completion_status,
                model_used=_model_label if "_model_label" in dir() else "",
                campaign_id=args.run if hasattr(args, "run") and args.run else "",
            )
        except Exception as _cr_e:
            print(f"[completion record] failed (non-fatal): {_cr_e}")

        _write_report_footer(
            book_key=book_key,
            chunks_done=chunks_done,
            total_deposited=total_deposited,
            errors=errors,
            status=_completion_status,
        )
        print(f"Report: {_report_path(book_key)}")

    print("─" * 60)
    if args.run:
        print(
            f"Done. {chunks_done} chunks processed. {total_deposited} total nodes deposited. {errors} errors."
        )
        print(f"Progress saved: {_progress_path(book_key)}")
    else:
        print(f"Dry run: {chunks_done} chunks would be processed.")
        print("Add --run to execute.")


def main():
    parser = argparse.ArgumentParser(
        description="Book learner — extract graph nodes from a book"
    )
    parser.add_argument("--book", default="", help="Book title (fuzzy search)")
    parser.add_argument(
        "--calibre-id", type=int, default=None, help="Exact Calibre book ID"
    )
    parser.add_argument("--url", default="", help="URL to fetch and learn (web source)")
    parser.add_argument("--title", default="", help="Title override for URL sources")
    parser.add_argument(
        "--chunk", type=int, default=15, help="Sentences per chunk (default 15)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="Seconds between API calls (default 1.5)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("BOOK_LEARNER_MODEL", "openai/gpt-4o-mini"),
        help="LLM model (default: BOOK_LEARNER_MODEL env or gpt-4o-mini)",
    )
    parser.add_argument(
        "--pass2",
        action="store_true",
        help="D333: situated re-read — Igor reads as Igor with context injection",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local Ollama instead of OpenRouter (free, no API cost)",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Actually call API and deposit (default: dry run)",
    )
    parser.add_argument(
        "--resume", action="store_true", help="Skip chunks already processed"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Max chunks to process (0=all)"
    )
    parser.add_argument(
        "--start", type=int, default=0, help="Start at sentence position"
    )
    args = parser.parse_args()

    if not args.book and not args.calibre_id and not args.url:
        parser.error("Provide --book, --calibre-id, or --url")

    run(args)


if __name__ == "__main__":
    main()

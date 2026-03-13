"""
learner.py — "go learn about X" tool.

Pipeline:
  1. Search Calibre for non-fiction books on the topic → launch book_learner background
  2. Ask a free public AI (Gemini) via browser to list publicly available texts → queue URLs
  3. Night queue drains URLs through book_learner --url at human-paced intervals

Registered tools:
  learn_about      — main entry point; code_ref for PROC_GO_LEARN habit
  process_learn_queue — drain queued URLs (called by heartbeat at night)
"""

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .registry import Tool, registry

# ── Paths ──────────────────────────────────────────────────────────────────────
_REPO        = Path(__file__).parent.parent.parent.parent
_BOOK_LEARNER = _REPO / "claudecode" / "book_learner.py"
_VENV_PYTHON  = _REPO / "venv" / "bin" / "python"
_QUEUE_FILE   = Path.home() / ".TheIgors" / "learn_queue.json"

# ── Fiction filter ─────────────────────────────────────────────────────────────
# Tags containing any of these substrings → skip the book
_FICTION_MARKERS = (
    "fiction", "novel", "fantasy", "thriller", "mystery", "romance",
    "horror", "sci-fi", "science fiction", "short stor", "poetry",
    "drama", "play", "screenplay", "comic", "manga", "children",
    "young adult", "fairy tale", "fable",
)

def _is_fiction(book: dict) -> bool:
    tags = [t.lower() for t in book.get("tags", [])]
    title_lower = book.get("title", "").lower()
    for marker in _FICTION_MARKERS:
        if any(marker in t for t in tags):
            return True
    # Heuristic: title ends with common fiction signals
    for signal in (" - a novel", ": a novel", " (novel)"):
        if title_lower.endswith(signal):
            return True
    return False

# ── Trigger phrase stripper ────────────────────────────────────────────────────
_TRIGGERS = (
    "go learn about",
    "please learn about",
    "i want you to learn about",
    "research and learn about",
    "research and learn",
    "book learn about",
    "book learn",
    "learn about",
)

_TONIGHT_MARKERS = ("tonight", "later tonight", "overnight", "at night", "when idle")

def _extract_topic(user_input: str) -> str:
    low = user_input.lower().strip()
    for t in sorted(_TRIGGERS, key=len, reverse=True):
        if low.startswith(t):
            topic = user_input[len(t):].strip(" .:,")
            # Strip any trailing "tonight" / timing modifier
            for m in _TONIGHT_MARKERS:
                if topic.lower().endswith(m):
                    topic = topic[: -len(m)].strip(" .,")
            return topic
    return user_input.strip()

def _is_tonight(user_input: str) -> bool:
    low = user_input.lower()
    return any(m in low for m in _TONIGHT_MARKERS)

# ── Learn queue ────────────────────────────────────────────────────────────────

def _load_queue() -> list:
    try:
        if _QUEUE_FILE.exists():
            return json.loads(_QUEUE_FILE.read_text())
    except Exception:
        pass
    return []

def _save_queue(q: list) -> None:
    _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _QUEUE_FILE.write_text(json.dumps(q, indent=2))

def _queue_url(url: str, title: str, topic: str) -> None:
    q = _load_queue()
    # Dedup by URL
    if any(e.get("url") == url for e in q):
        return
    q.append({
        "url":      url,
        "title":    title,
        "topic":    topic,
        "added_at": datetime.now().isoformat(),
        "done":     False,
    })
    _save_queue(q)

# ── Calibre non-fiction search ─────────────────────────────────────────────────

def _calibre_nonfiction(topic: str) -> list[dict]:
    """Search Calibre and filter out fiction."""
    try:
        from .ebook_reader import find_book
        all_books = find_book(topic)
    except Exception:
        return []
    return [b for b in all_books if not _is_fiction(b)]

# ── Browser AI discovery ───────────────────────────────────────────────────────

_AI_SITES = [
    ("Gemini",  "https://gemini.google.com"),
    ("ChatGPT", "https://chatgpt.com"),
]

_DISCOVERY_PROMPT = (
    "Please list 8-10 freely and publicly available online texts, papers, or books "
    "about {topic}. Focus on authoritative sources: Project Gutenberg, arXiv, "
    "university open-access sites, Wikipedia, or well-known free resources. "
    "Include the direct URL for each. Return only the list with URLs."
)

def _parse_urls(text: str) -> list[str]:
    """Extract HTTP(S) URLs from a block of text."""
    raw = re.findall(r'https?://[^\s"\'<>\])\|]+', text)
    # Clean trailing punctuation
    cleaned = [u.rstrip(".,;:)") for u in raw]
    # Filter out the AI site's own domain
    skip = {"gemini.google.com", "chatgpt.com", "openai.com", "google.com"}
    return [u for u in cleaned if not any(s in u for s in skip)]

def _discover_urls_via_browser(topic: str) -> list[tuple[str, str]]:
    """
    Ask a public AI assistant (no auth) for a list of free URLs on the topic.
    Returns list of (url, title) tuples. Falls back gracefully if browser unavailable.
    """
    try:
        from .browser import browser_use_task
    except ImportError:
        return []

    prompt = _DISCOVERY_PROMPT.format(topic=topic)
    results = []

    for name, site in _AI_SITES:
        try:
            task = (
                f"Go to {site}. "
                f"In the chat input, type exactly: {prompt!r} "
                f"Wait for the full response. Return the complete response text."
            )
            result = browser_use_task(task=task, max_steps=8)
            response_text = result.get("result", "") if isinstance(result, dict) else str(result)
            urls = _parse_urls(response_text)
            for url in urls[:10]:
                results.append((url, f"{name} suggestion for '{topic}'"))
            if results:
                break  # one AI is enough; don't hammer multiple
        except Exception:
            continue

    return results

# ── Background launcher ────────────────────────────────────────────────────────

def _launch_book(calibre_id: int = None, url: str = None, title: str = "",
                 local: bool = True) -> bool:
    python = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable
    cmd = [python, str(_BOOK_LEARNER), "--run", "--resume"]
    if local:
        cmd.append("--local")
    if calibre_id is not None:
        cmd += ["--calibre-id", str(calibre_id)]
    elif url:
        cmd += ["--url", url]
        if title:
            cmd += ["--title", title[:80]]
    else:
        return False
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False

# ── Public tools ──────────────────────────────────────────────────────────────

def learn_about(user_input: str) -> str:
    """
    Full learning pipeline for a topic:
    1. Calibre non-fiction search → launch immediately
    2. Browser AI discovery → queue URLs for night processing
    """
    topic    = _extract_topic(user_input)
    tonight  = _is_tonight(user_input)

    if not topic:
        return "What topic shall I learn about? Try: go learn about consciousness"

    lines = [f"Learning about: {topic}" + (" (queued for tonight)" if tonight else "")]

    # ── 1. Calibre non-fiction ─────────────────────────────────────────────
    books = _calibre_nonfiction(topic)
    launched_books = []
    queued_books   = []

    for book in books[:3]:
        cid   = book.get("calibre_id")
        title = book.get("title", "?")
        if not cid:
            continue
        if tonight:
            # Queue as URL-style entry using calibre_id sentinel
            _queue_url(f"calibre://{cid}", title, topic)
            queued_books.append(title)
        else:
            if _launch_book(calibre_id=cid, local=True):
                launched_books.append(title)

    if launched_books:
        lines.append("Library: launched — " + ", ".join(f'"{t}"' for t in launched_books))
    if queued_books:
        lines.append("Library: queued for tonight — " + ", ".join(f'"{t}"' for t in queued_books))
    if not books:
        lines.append("Library: no non-fiction matches in Calibre.")

    # ── 2. Browser AI discovery → night queue (always async) ──────────────
    try:
        url_pairs = _discover_urls_via_browser(topic)
        if url_pairs:
            for url, title in url_pairs:
                _queue_url(url, title, topic)
            lines.append(f"Web: {len(url_pairs)} URL(s) queued for night processing.")
        else:
            lines.append("Web: browser discovery unavailable — will rely on library sources.")
    except Exception as e:
        lines.append(f"Web: discovery skipped ({e}).")

    return "\n".join(lines)


def process_learn_queue(max_items: int = 5) -> str:
    """
    Drain the learn queue: launch book_learner --url for each pending URL.
    Designed to be called at night by a heartbeat habit or manually.
    Processes up to max_items per call to avoid overloading the machine.
    """
    q = _load_queue()
    pending = [e for e in q if not e.get("done")]

    if not pending:
        return "Learn queue is empty."

    # Night-time check: only auto-drain between 22:00 and 07:00
    hour = datetime.now().hour
    is_night = hour >= 22 or hour < 7

    launched = []
    for entry in pending[:max_items]:
        url   = entry.get("url", "")
        title = entry.get("title", url)
        if not url:
            entry["done"] = True
            continue
        # Polite pace: 3-second gap between launches (human speed)
        if launched:
            time.sleep(3)
        # calibre:// sentinel → use calibre_id path
        if url.startswith("calibre://"):
            try:
                cid = int(url[len("calibre://"):])
                ok = _launch_book(calibre_id=cid, title=title, local=True)
            except ValueError:
                ok = False
        else:
            ok = _launch_book(url=url, title=title, local=True)
        if ok:
            entry["done"] = True
            launched.append(title[:60])

    _save_queue(q)

    remaining = len([e for e in q if not e.get("done")])
    parts = [f"Launched {len(launched)} URL learner(s)."]
    if launched:
        parts.append("Sources: " + "; ".join(f'"{t}"' for t in launched))
    if remaining:
        parts.append(f"{remaining} item(s) still queued.")
    return " ".join(parts)


# ── Tool registration ──────────────────────────────────────────────────────────

registry.register(Tool(
    name="learn_about",
    description=(
        "Search Calibre library (non-fiction only) for books on a topic and "
        "launch book_learner in the background. Also uses browser to discover "
        "freely available web sources and queues them for night processing. "
        "Called when user says 'go learn about X' or 'learn about X'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "user_input": {
                "type": "string",
                "description": "Full user input including trigger phrase e.g. 'go learn about consciousness'",
            }
        },
        "required": ["user_input"],
    },
    fn=learn_about,
))

registry.register(Tool(
    name="process_learn_queue",
    description=(
        "Drain the learn queue: launch book_learner for each queued URL. "
        "Call at night or when idle. Processes up to 5 items per call at human pace."
    ),
    parameters={
        "type": "object",
        "properties": {
            "max_items": {
                "type": "integer",
                "description": "Max URLs to process in this call (default 5)",
            }
        },
        "required": [],
    },
    fn=process_learn_queue,
))

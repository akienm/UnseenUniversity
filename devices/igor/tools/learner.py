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
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from devices.igor.tools.registry import Tool, registry
from ..memory.db_proxy import DatabaseProxy, make_home_proxy, make_local_proxy
from ..paths import paths

# ── Igor DB proxy singleton (G-DB1 W1) ────────────────────────────────────────
_IGOR_DB_PROXY: Optional[DatabaseProxy] = None
_IGOR_DB_PROXY_LOCK = threading.Lock()
_LOCAL_DB_PROXY: Optional[DatabaseProxy] = None
_LOCAL_DB_PROXY_LOCK = threading.Lock()


def _igor_db_proxy() -> DatabaseProxy:
    """Return (or create) the singleton DatabaseProxy for the home DB."""
    global _IGOR_DB_PROXY
    with _IGOR_DB_PROXY_LOCK:
        if _IGOR_DB_PROXY is None:
            _IGOR_DB_PROXY = make_home_proxy()
    return _IGOR_DB_PROXY


def _local_db_proxy() -> DatabaseProxy:
    """Return (or create) the singleton proxy for instance-schema tables (twm_observations, ring_memory)."""
    global _LOCAL_DB_PROXY
    with _LOCAL_DB_PROXY_LOCK:
        if _LOCAL_DB_PROXY is None:
            _LOCAL_DB_PROXY = make_local_proxy()
    return _LOCAL_DB_PROXY


# ── Paths ──────────────────────────────────────────────────────────────────────
_REPO = Path(__file__).parent.parent.parent.parent
_BOOK_LEARNER = _REPO / "lab" / "claudecode" / "book_learner.py"
_DRAIN_SCRIPT = _REPO / "lab" / "claudecode" / "drain_learn_queue.py"
_VENV_PYTHON = _REPO / "venv" / "bin" / "python"
_QUEUE_FILE = paths().learn_queue
_DRAIN_PID = paths().drain_pid

# ── Fiction filter ─────────────────────────────────────────────────────────────
# Tags containing any of these substrings → skip the book
_FICTION_MARKERS = (
    "fiction",
    "novel",
    "fantasy",
    "thriller",
    "mystery",
    "romance",
    "horror",
    "sci-fi",
    "science fiction",
    "short stor",
    "poetry",
    "drama",
    "play",
    "screenplay",
    "comic",
    "manga",
    "children",
    "young adult",
    "fairy tale",
    "fable",
)

# Known fiction authors — skip regardless of tags (for authors with missing/wrong tags)
_FICTION_AUTHORS = {
    "anthony, piers",
    "brooks, terry",
    "burroughs, edgar rice",
    "card, orson scott",
    "king, stephen",
    "koontz, dean",
    "lackey, mercedes",
    "leiber, fritz",
    "martin, george r. r.",
    "mccaffrey, anne",
    "pratchett, terry",
    "stasheff, christopher",
    "turtledove, harry",
    "watt-evans, laurence",
    "weber, david",
}


def _is_fiction(book: dict) -> bool:
    tags = [t.lower() for t in book.get("tags", [])]
    title_lower = book.get("title", "").lower()
    author_lower = book.get("author_sort", book.get("authors", "")).lower()

    # Known fiction author
    for author in _FICTION_AUTHORS:
        if author in author_lower:
            return True

    # Tag-based check
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
    # G-OVN-5: strip CC bridge thread-context prefix before processing
    # Formats: "[Thread context: xxx]\n\n...", "[Web message from X]: ...", "[claude-code]: ..."
    text = re.sub(r"^\[Thread context:[^\]]*\]\s*", "", user_input, flags=re.IGNORECASE)
    text = re.sub(r"^\[[^\]]*\]:\s*", "", text, flags=re.IGNORECASE)
    text = text.strip()

    low = text.lower()
    # Search anywhere in the input — handles any remaining prefix
    for t in sorted(_TRIGGERS, key=len, reverse=True):
        idx = low.find(t)
        if idx != -1:
            topic = text[idx + len(t) :].strip(" .:,")
            # Strip any trailing "tonight" / timing modifier
            for m in _TONIGHT_MARKERS:
                if topic.lower().endswith(m):
                    topic = topic[: -len(m)].strip(" .,")
            if len(topic.split()) >= 2:  # require at least 2 words to be a real topic
                return topic
    # No trigger found — return as-is only if it looks like a real topic (3+ words)
    words = text.split()
    if len(words) >= 3:
        return text
    return ""


def _is_tonight(user_input: str) -> bool:
    low = user_input.lower()
    return any(m in low for m in _TONIGHT_MARKERS)


# ── Learn queue ────────────────────────────────────────────────────────────────


def _load_queue() -> list:
    try:
        if _QUEUE_FILE.exists():
            return json.loads(_QUEUE_FILE.read_text())
    except Exception as _e:
        try:
            from ..cognition.forensic_logger import log_error as _le

            _le(
                kind="LEARN_QUEUE_LOAD_FAIL",
                detail=str(_e),
                source="learner._load_queue",
            )
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT", detail=f"devices/igor/tools/learner.py: {_bare_e}"
            )
    return []


def _save_queue(q: list) -> None:
    _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _QUEUE_FILE.write_text(json.dumps(q, indent=2))


def _queue_url(url: str, title: str, topic: str, cloud_ok: bool = True) -> None:
    q = _load_queue()
    # Dedup by URL
    if any(e.get("url") == url for e in q):
        return
    q.append(
        {
            "url": url,
            "title": title,
            "topic": topic,
            "added_at": datetime.now().isoformat(),
            "cloud_ok": cloud_ok,  # D071: False = local-only overnight; True = cloud OK (now mode)
            "done": False,
        }
    )
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
    ("Gemini", "https://gemini.google.com"),
    ("ChatGPT", "https://chatgpt.com"),
]

_DISCOVERY_PROMPT = (
    "List 8-10 freely available online texts or papers about {topic}. "
    "Sources: arXiv, Project Gutenberg, Wikipedia, university open-access sites. "
    "OUTPUT FORMAT: one bare URL per line, nothing else. No titles, no descriptions, "
    "no markdown, no numbering. Just URLs, one per line. Example:\n"
    "https://arxiv.org/abs/1234.5678\n"
    "https://en.wikipedia.org/wiki/Example\n"
    "Start your response with the first URL immediately."
)


def _parse_urls(text: str) -> list[str]:
    """Extract HTTP(S) URLs from a block of text — bare URLs and markdown [text](url)."""
    # Bare URLs
    raw = re.findall(r'https?://[^\s"\'<>\])\|]+', text)
    # Markdown links: [title](url)
    md_urls = re.findall(r"\[[^\]]*\]\((https?://[^)]+)\)", text)
    raw.extend(md_urls)
    # Clean trailing punctuation
    cleaned = [u.rstrip(".,;:)") for u in raw]
    # Deduplicate, preserving order
    seen: set[str] = set()
    deduped = []
    for u in cleaned:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    # Filter out the AI site's own domain
    skip = {"gemini.google.com", "chatgpt.com", "openai.com", "google.com"}
    return [u for u in deduped if not any(s in u for s in skip)]


def _discover_urls_direct(topic: str) -> list[tuple[str, str]]:
    """
    Build a short list of directly-constructable URLs for a topic without needing
    AI assistance. Hits arXiv search and Wikipedia. Fast and reliable.
    Returns list of (url, title) tuples.
    """
    slug = topic.strip().replace(" ", "+")
    wiki_slug = topic.strip().replace(" ", "_").title()
    return [
        (
            f"https://arxiv.org/search/?query={slug}&searchtype=all&order=-announced_date_first",
            f"arXiv search: {topic}",
        ),
        (
            f"https://en.wikipedia.org/wiki/{wiki_slug}",
            f"Wikipedia: {topic}",
        ),
        (
            f"https://www.gutenberg.org/ebooks/search/?query={slug}",
            f"Project Gutenberg: {topic}",
        ),
    ]


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

    import logging as _log

    _browser_log = _log.getLogger("browser_use")

    for name, site in _AI_SITES:
        try:
            task = (
                f"Go to {site}. "
                f"In the chat input, type exactly: {prompt!r} "
                f"Wait for the full response. Return the complete response text."
            )
            _browser_log.info(
                f"[learner] starting browser discovery via {name} for topic={topic!r}"
            )
            result = browser_use_task(task_description=task, max_steps=8)
            _browser_log.info(
                f"[learner] browser_use_task raw result: {str(result)[:300]}"
            )
            if isinstance(result, str):
                import json as _json

                try:
                    result = _json.loads(result)
                except Exception as _bare_e:
                    log_error(
                        kind="BARE_EXCEPT",
                        detail=f"devices/igor/tools/learner.py: {_bare_e}",
                    )
            if isinstance(result, dict):
                status = result.get("status", "?")
                if status != "success":
                    _browser_log.warning(
                        f"[learner] {name} browser task status={status} error={result.get('error', '')[:200]}"
                    )
                response_text = result.get("result", "")
            else:
                response_text = str(result)
            urls = _parse_urls(response_text)
            _browser_log.info(
                f"[learner] {name}: parsed {len(urls)} URLs from response"
            )
            for url in urls[:10]:
                results.append((url, f"{name} suggestion for '{topic}'"))
            if results:
                break  # one AI is enough; don't hammer multiple
        except Exception as e:
            _browser_log.warning(f"[learner] browser discovery via {name} raised: {e}")
            continue

    return results


# ── Queue runner ───────────────────────────────────────────────────────────────


def _queue_runner_alive() -> bool:
    """True if drain_learn_queue.py is already running (PID file check)."""
    try:
        if not _DRAIN_PID.exists():
            return False
        pid = int(_DRAIN_PID.read_text().strip())
        # Check if process exists
        import os as _os

        _os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


def _launch_queue_runner(delay: float = 60.0) -> bool:
    """
    Spawn drain_learn_queue.py as a detached background process.
    No-op if one is already running (PID file guard).
    Returns True if launched, False if already running or failed.
    """
    if _queue_runner_alive():
        return False  # already running

    python = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable
    cmd = [python, str(_DRAIN_SCRIPT), "--delay", str(delay)]
    try:
        log_dir = paths().logs
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "drain_learn_queue.log", "a")
        subprocess.Popen(cmd, stdout=log_file, stderr=log_file, start_new_session=True)
        return True
    except Exception:
        return False


# ── Background launcher ────────────────────────────────────────────────────────


def _launch_book(
    calibre_id: int = None, url: str = None, title: str = "", local: bool = True
) -> bool:
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
        log_dir = paths().logs
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "book_learner.log", "a")
        subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
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
    topic = _extract_topic(user_input)
    tonight = _is_tonight(user_input)
    cloud_ok = (
        not tonight
    )  # D071: "tonight" = local-only; "now" (no tonight marker) = cloud OK

    if not topic:
        return "What topic shall I learn about? Try: go learn about consciousness"

    lines = [
        f"Learning about: {topic}"
        + (
            " (queued for tonight, local-only)"
            if tonight
            else " (cloud OK — starting now)"
        )
    ]

    # ── 0. Set/clear cloud_ok override ────────────────────────────────────
    if not tonight:
        # "now" mode — activate cloud_ok override so drain runner and book_learner use cloud
        try:
            from ..cognition.cloud_mode import set_cloud_ok_override

            status = set_cloud_ok_override(ttl_hours=4.0, reason="learn_about now")
            lines.append(f"Cloud: {status}")
        except Exception as e:
            lines.append(f"Cloud: override failed ({e}) — will use local.")
    else:
        # "tonight" mode — clear any existing override so night runner stays local
        try:
            from ..cognition.cloud_mode import clear_cloud_ok_override

            clear_cloud_ok_override(reason="learn_about tonight")
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT", detail=f"devices/igor/tools/learner.py: {_bare_e}"
            )

    # ── 1. Calibre non-fiction ─────────────────────────────────────────────
    books = _calibre_nonfiction(topic)
    launched_books = []
    queued_books = []

    for book in books[:3]:
        cid = book.get("calibre_id")
        title = book.get("title", "?")
        if not cid:
            continue
        if tonight:
            # Queue as URL-style entry using calibre_id sentinel
            _queue_url(f"calibre://{cid}", title, topic, cloud_ok=False)
            queued_books.append(title)
        else:
            if _launch_book(calibre_id=cid, local=True):
                launched_books.append(title)

    if launched_books:
        lines.append(
            "Library: launched — " + ", ".join(f'"{t}"' for t in launched_books)
        )
    if queued_books:
        lines.append(
            "Library: queued for tonight — " + ", ".join(f'"{t}"' for t in queued_books)
        )
    if not books:
        lines.append("Library: no non-fiction matches in Calibre.")

    # ── 2. URL discovery → night queue ────────────────────────────────────
    # Phase A: direct arXiv/Wikipedia/Gutenberg (always works, no AI needed)
    # Phase B: browser AI discovery for additional sources
    urls_queued = 0
    try:
        direct_pairs = _discover_urls_direct(topic)
        for url, title in direct_pairs:
            _queue_url(url, title, topic, cloud_ok=cloud_ok)
        urls_queued += len(direct_pairs)
        if direct_pairs:
            lines.append(
                f"Web: {len(direct_pairs)} direct URL(s) queued (arXiv/Wikipedia/Gutenberg)."
            )
    except Exception as e:
        lines.append(f"Web: direct URL discovery error ({e}).")

    try:
        url_pairs = _discover_urls_via_browser(topic)
        if url_pairs:
            for url, title in url_pairs:
                _queue_url(url, title, topic, cloud_ok=cloud_ok)
            urls_queued += len(url_pairs)
            lines.append(f"Web: {len(url_pairs)} AI-discovered URL(s) queued.")
        else:
            lines.append(
                "Web: browser AI discovery returned no URLs — check ~/.unseen_university/logs/browser_use.log."
            )
    except Exception as e:
        lines.append(f"Web: browser discovery error ({e}) — check browser_use.log.")

    # ── 3. Spawn queue runner if anything was queued ───────────────────────
    anything_queued = bool(queued_books) or urls_queued > 0
    if anything_queued:
        launched_runner = _launch_queue_runner(delay=60.0)
        if launched_runner:
            lines.append(
                "Background queue runner started — will drain items at 60s intervals."
            )
        else:
            lines.append("Queue runner already active.")

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
        url = entry.get("url", "")
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
                cid = int(url[len("calibre://") :])
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

registry.register(
    Tool(
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
    )
)

registry.register(
    Tool(
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
    )
)


def drain_learn_queue(**_kwargs) -> str:
    """
    Spawn the background queue runner (drain_learn_queue.py) if not already running.
    Shows queue status and whether the runner was started or was already active.
    """
    q = _load_queue()
    pending = [e for e in q if not e.get("done")]

    if not pending:
        return "Learn queue is empty — nothing to drain."

    if _queue_runner_alive():
        return f"Queue runner already active. {len(pending)} item(s) pending."

    launched = _launch_queue_runner(delay=60.0)
    if launched:
        topics = sorted({e.get("topic", "?") for e in pending if e.get("topic")})
        topic_str = ", ".join(f'"{t}"' for t in topics[:5])
        return (
            f"Background queue runner started. "
            f"{len(pending)} item(s) to process (topics: {topic_str or '?'}). "
            f"60s between launches. Check ~/.unseen_university/logs/drain_learn_queue.log for progress."
        )
    return "Failed to launch queue runner — check logs."


registry.register(
    Tool(
        name="drain_learn_queue",
        description=(
            "Start the background learning queue runner. Drains ~/.unseen_university/learn_queue.json "
            "by launching book_learner for each pending item at 60-second intervals. "
            "Safe to call multiple times — won't spawn duplicates. "
            "Use after 'go learn about X tonight' to kick off overnight processing."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=drain_learn_queue,
    )
)


# ── list_absorbed_books ────────────────────────────────────────────────────────


def list_absorbed_books(**_kwargs) -> str:
    """Return a summary of books/sources that have been absorbed via book_learner."""
    try:
        with _igor_db_proxy()() as conn:
            rows = conn.execute("""
                SELECT metadata
                FROM memories
                WHERE memory_type IN ('FACTUAL','INTERPRETIVE','PROCEDURAL')
                  AND source = 'reading'
            """).fetchall()
    except Exception as e:
        return f"Error querying absorbed books: {e}"

    # Group by book_title in Python — avoids json_extract (SQLite-only) vs ->>' (Postgres-only)
    from collections import defaultdict

    book_counts: dict = defaultdict(lambda: {"author": "", "count": 0})
    for row in rows:
        raw = row["metadata"]
        meta = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
        title = meta.get("book_title")
        if not title:
            continue
        book_counts[title]["author"] = meta.get("book_author", "")
        book_counts[title]["count"] += 1

    # Also query reading_list for completed items
    completed_items: list[dict] = []
    try:
        with _igor_db_proxy()() as conn:
            rl_rows = conn.execute("""
                SELECT source, title, completed_at
                FROM reading_list
                WHERE status = 'completed'
                  AND (source IS NULL OR source NOT LIKE 'trace://%')
                ORDER BY completed_at DESC NULLS LAST
            """).fetchall()
            for r in rl_rows:
                completed_items.append(
                    {
                        "source": r[0],
                        "title": r[1],
                        "completed_at": r[2],
                    }
                )
    except Exception as _e:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"learner.py:731: {_e}")

    lines = []

    # Section 1: reading_list completed
    if completed_items:
        lines.append(f"Reading pipeline — {len(completed_items)} completed:")
        for item in completed_items[:20]:
            title = item.get("title") or item.get("source", "")[:60]
            completed_at = (item.get("completed_at") or "")[:10]
            lines.append(f"  ✓ {title}  ({completed_at})")
        if len(completed_items) > 20:
            lines.append(f"  … and {len(completed_items) - 20} more")
    else:
        lines.append("Reading pipeline — no completed items yet.")

    # Section 2: memory nodes deposited from reading
    if book_counts:
        lines.append(f"\nKnowledge nodes built — {len(book_counts)} source(s):")
        total_nodes = 0
        for title, info in sorted(book_counts.items(), key=lambda x: -x[1]["count"]):
            author = info["author"]
            count = info["count"]
            total_nodes += count
            entry = f"  • {title}"
            if author:
                entry += f" — {author}"
            entry += f"  ({count} nodes)"
            lines.append(entry)
        lines.append(f"  Total: {total_nodes} knowledge nodes")

    # Also show queue
    queue_path = paths().learn_queue
    if queue_path.exists():
        try:
            queue = json.loads(queue_path.read_text())
            active = [e for e in queue if not e.get("done")]
            if active:
                lines.append(f"\nCurrently in drain queue: {len(active)} item(s)")
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT", detail=f"devices/igor/tools/learner.py: {_bare_e}"
            )

    return "\n".join(lines)


registry.register(
    Tool(
        name="list_absorbed_books",
        description=(
            "List all books and sources Igor has absorbed via book_learner, "
            "with node counts per source. Also shows the pending learn queue."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=list_absorbed_books,
    )
)


# ── reading_list tools ─────────────────────────────────────────────────────────


def get_reading_list(**kwargs) -> str:
    """Return the reading list, optionally filtered by status or book_type."""
    status_filter = kwargs.get("status")  # e.g. "pending", "in_progress", "completed"
    type_filter = kwargs.get("book_type")  # "fiction" | "nonfiction"
    try:
        sql = "SELECT * FROM reading_list WHERE 1=1"
        params = []
        if status_filter:
            sql += " AND status = %s"
            params.append(status_filter)
        if type_filter:
            sql += " AND book_type = %s"
            params.append(type_filter)
        sql += " ORDER BY priority, id"
        with _igor_db_proxy()() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        return f"Error reading reading_list: {e}"

    if not rows:
        return "No entries match."

    _STATUS_ICON = {
        "pending": "○",
        "in_progress": "▶",
        "completed": "✓",
        "needs_acquisition": "?",
        "paused": "‖",
    }
    lines = []
    for r in rows:
        icon = _STATUS_ICON.get(r["status"], "·")
        rate = "slow" if r["reading_rate"] == "slow" else ""
        label = f"{icon} [{r['id']}] {r['title']} — {r['author'] or '?'}"
        if rate:
            label += f"  ({rate})"
        lines.append(label)
        if r["emotional_significance"]:
            lines.append(f"    ↳ {r['emotional_significance']}")
    return "\n".join(lines)


def add_to_reading_list(**kwargs) -> str:
    """Add a book to the reading list."""
    import time as _time

    title = kwargs.get("title", "").strip()
    author = kwargs.get("author", "")
    source = kwargs.get("source", "")
    if not title:
        return "title is required."
    try:
        with _igor_db_proxy()() as conn:
            row = conn.execute(
                "SELECT MAX(CAST(SUBSTRING(id FROM 4) AS INTEGER)) FROM reading_list WHERE id ~ '^RL_[0-9]+$'"
            ).fetchone()
            max_n = row[0] or 0
            new_id = f"RL_{max_n + 1:03d}"
            conn.execute(
                """
                INSERT INTO reading_list
                (id, title, author, source, book_type, reading_rate, priority, status,
                 emotional_significance, encoding_arousal, notes, added_by, added_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
                (
                    new_id,
                    title,
                    author,
                    source,
                    kwargs.get("book_type", "nonfiction"),
                    kwargs.get("reading_rate", "fast"),
                    kwargs.get("priority", 50),
                    kwargs.get("status", "pending"),
                    kwargs.get("emotional_significance"),
                    float(kwargs.get("encoding_arousal", 0.3)),
                    kwargs.get("notes"),
                    kwargs.get("added_by", "igor"),
                    _time.strftime("%Y-%m-%dT%H:%M:%S"),
                ),
            )
        return f"Added {new_id}: {title}"
    except Exception as e:
        return f"Error adding to reading_list: {e}"


def update_reading_status(**kwargs) -> str:
    """Update the status of a reading list entry."""
    import time as _time

    rl_id = kwargs.get("id", "").strip()
    status = kwargs.get("status", "").strip()
    if not rl_id or not status:
        return "id and status are required."
    valid = ("pending", "in_progress", "completed", "needs_acquisition", "paused")
    if status not in valid:
        return f"status must be one of: {', '.join(valid)}"
    try:
        ts = _time.strftime("%Y-%m-%dT%H:%M:%S")
        with _igor_db_proxy()() as conn:
            if status == "in_progress":
                cur = conn.execute(
                    "UPDATE reading_list SET status=%s, started_at=%s WHERE id=%s",
                    (status, ts, rl_id),
                )
            elif status == "completed":
                cur = conn.execute(
                    "UPDATE reading_list SET status=%s, completed_at=%s WHERE id=%s",
                    (status, ts, rl_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE reading_list SET status=%s WHERE id=%s", (status, rl_id)
                )
            changed = cur.rowcount
        return (
            f"Updated {rl_id} → {status}"
            if changed
            else f"No entry found with id={rl_id}"
        )
    except Exception as e:
        return f"Error updating reading_list: {e}"


registry.register(
    Tool(
        name="get_reading_list",
        description="Show Igor's permanent reading list. Filter by status (pending/in_progress/completed/needs_acquisition/paused) or book_type (fiction/nonfiction).",
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status"},
                "book_type": {
                    "type": "string",
                    "description": "Filter by fiction or nonfiction",
                },
            },
            "required": [],
        },
        fn=get_reading_list,
    )
)

registry.register(
    Tool(
        name="add_to_reading_list",
        description="Add a book or resource to Igor's permanent reading list.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "author": {"type": "string"},
                "source": {
                    "type": "string",
                    "description": "calibre://ID, file:///path, or https://...",
                },
                "book_type": {"type": "string", "description": "fiction or nonfiction"},
                "reading_rate": {"type": "string", "description": "slow or fast"},
                "priority": {
                    "type": "integer",
                    "description": "Lower = sooner. Default 50.",
                },
                "emotional_significance": {"type": "string"},
                "encoding_arousal": {"type": "number", "description": "0.0-1.0"},
                "notes": {"type": "string"},
                "status": {"type": "string"},
            },
            "required": ["title"],
        },
        fn=add_to_reading_list,
    )
)

registry.register(
    Tool(
        name="update_reading_status",
        description="Update the status of a reading list entry (e.g. mark as in_progress or completed).",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Reading list ID e.g. RL_001"},
                "status": {
                    "type": "string",
                    "description": "queued | in_progress | completed | needs_acquisition | paused",
                },
            },
            "required": ["id", "status"],
        },
        fn=update_reading_status,
    )
)


# ── annotate_learning (#252) ───────────────────────────────────────────────────


def annotate_learning(**kwargs) -> str:
    """
    Deposit a personal EXPERIENTIAL memory recording whether an approach worked.
    Triggered by 'this worked', 'didn't work', 'mark that', etc.
    Stores source='user_annotated', certainty=0.95 so it outweighs generic FACTUAL.
    """
    import uuid as _uuid
    import time as _time

    outcome = kwargs.get("outcome", "").strip()
    worked = kwargs.get("worked", True)
    notes = kwargs.get("notes", "").strip()

    if not outcome:
        return "Please describe what worked or didn't work."

    verdict = "worked for Akien" if worked else "did not work for Akien"
    narrative = f"Personal experience: {outcome} — {verdict}."
    if notes:
        narrative += f" {notes}"

    mem_id = str(_uuid.uuid4())[:8]
    metadata = {
        "worked": worked,
        "outcome": outcome,
        "notes": notes,
        "source": "user_annotated",
    }

    try:
        from ..memory.cortex import Cortex
        from ..memory.models import Memory, MemoryType
        from ..paths import paths as _paths

        cortex = Cortex()
        mem = Memory(
            id=mem_id,
            narrative=narrative,
            memory_type=MemoryType.EXPERIENTIAL,
            parent_id="CP5",  # inner state — "I have an inner life"
            valence=0.6 if worked else -0.3,
            activation_count=1,
            metadata=metadata,
            source="user_annotated",
            certainty=0.95,  # high confidence — first-person experience
            context_of_encoding="akien_annotation",
        )
        cortex.store(mem)
        return f"Noted. Deposited as personal experience [{mem_id}]: {narrative[:120]}"
    except Exception as e:
        return f"Error depositing experience: {e}"


def learn_top_gap(**_kwargs) -> str:
    """
    Self-directed curiosity drain. Reads the highest-salience unexpired NARRATIVE_GAP
    from TWM (twm_observations), extracts the question, adds it to reading_list.
    Called by SchedulerSource via PROC_CURIOSITY_DRAIN every 30 min.
    """
    import time as _time

    try:
        with _local_db_proxy()() as conn:
            row = conn.execute(
                """SELECT content_csb FROM twm_observations
                   WHERE content_csb LIKE %s
                     AND (expires_at IS NULL OR expires_at > to_char(NOW(), 'YYYY-MM-DD"T"HH24:MI:SS'))
                   ORDER BY salience DESC LIMIT 1""",
                ("NARRATIVE_GAP|%",),
            ).fetchone()
    except Exception as e:
        return f"[learn_top_gap] DB error: {e}"

    if not row:
        return "[learn_top_gap] no active gaps — nothing to queue"

    content = row[0] if isinstance(row, (list, tuple)) else row["content_csb"]
    # Parse: NARRATIVE_GAP|question=...|salience=...|threat=...
    question = ""
    for part in content.split("|"):
        if part.startswith("question="):
            question = part[len("question=") :]
            break

    if not question:
        return f"[learn_top_gap] could not parse question from: {content[:80]}"

    result = add_to_reading_list(
        title=question,
        book_type="curiosity",
        encoding_arousal=0.5,
        priority=30,
        added_by="igor_self",
        notes="Self-queued from NARRATIVE_GAP",
    )

    try:
        from ..cognition.forensic_logger import log_anomaly as _la

        _la(kind="CURIOSITY_QUEUED", detail=f"queued: {question[:120]} → {result}")
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"learner.py:1091: {_exc}")

    return result


registry.register(
    Tool(
        name="learn_top_gap",
        description="Self-directed curiosity drain: reads highest-salience NARRATIVE_GAP from TWM and queues topic to reading_list. Called by SchedulerSource via PROC_CURIOSITY_DRAIN.",
        parameters={"type": "object", "properties": {}},
        fn=learn_top_gap,
    )
)


# ── Arch doc ingest ────────────────────────────────────────────────────────────

# Files in design_docs_for_igor/ that Igor should know as self-defining material.
# Tuple: (relative_path, priority, encoding_arousal)
_ARCH_DOCS = [
    # Core identity — highest priority
    ("design_docs_for_igor/igor_identity_master.dsb", 1, 0.95),
    ("design_docs_for_igor/decisions_log.dsb", 1, 0.95),
    ("design_docs_for_igor/ethical_framework.dsb", 1, 0.95),
    # Architecture + capabilities
    ("design_docs_for_igor/architecture_root.dsb", 2, 0.90),
    ("design_docs_for_igor/capabilities_index.dsb", 2, 0.90),
    ("design_docs_for_igor/cognition_pipeline.dsb", 2, 0.90),
    ("design_docs_for_igor/engram_language.dsb", 2, 0.90),
    ("design_docs_for_igor/inertia_registry.dsb", 2, 0.88),
    # Subsystems + supporting docs
    ("design_docs_for_igor/gap_analysis.dsb", 3, 0.85),
    ("design_docs_for_igor/glossary.dsb", 3, 0.85),
    ("design_docs_for_igor/failure_modes.dsb", 3, 0.85),
    ("design_docs_for_igor/dev_process.dsb", 3, 0.83),
    ("design_docs_for_igor/subsystem_cognition.dsb", 3, 0.83),
    ("design_docs_for_igor/subsystem_memory.dsb", 3, 0.83),
    ("design_docs_for_igor/subsystem_inference.dsb", 3, 0.83),
    ("design_docs_for_igor/subsystem_tools.dsb", 3, 0.80),
    ("design_docs_for_igor/subsystem_reading.dsb", 3, 0.80),
    ("design_docs_for_igor/subsystem_self_edit.dsb", 3, 0.80),
    ("design_docs_for_igor/subsystem_cluster.dsb", 3, 0.78),
    ("design_docs_for_igor/subsystem_web_network.dsb", 3, 0.75),
]

_REPO_ROOT = Path(__file__).parent.parent.parent.parent


def ingest_arch_docs(**_kwargs) -> str:
    """
    Queue Igor's own architecture docs into reading_list at high encoding_arousal.
    Idempotent — skips entries already present by source URL.
    Called once at setup or on demand. NOT scheduled.
    """
    queued = []
    skipped = []
    errors = []

    # Fetch already-queued sources to avoid duplicates
    try:
        with _igor_db_proxy()() as conn:
            rows = conn.execute(
                "SELECT source FROM reading_list WHERE book_type = 'igor-architecture'"
            ).fetchall()
        existing_sources = {r[0] for r in rows if r[0]}
    except Exception as e:
        return f"[ingest_arch_docs] DB error fetching existing: {e}"

    for rel_path, priority, arousal in _ARCH_DOCS:
        full_path = _REPO_ROOT / rel_path
        source_url = f"file://{full_path}"

        if source_url in existing_sources:
            skipped.append(rel_path)
            continue

        if not full_path.exists():
            errors.append(f"missing: {rel_path}")
            continue

        # Use filename stem as title for readability
        title = full_path.stem.replace("_", " ").title()
        result = add_to_reading_list(
            title=title,
            source=source_url,
            book_type="igor-architecture",
            encoding_arousal=arousal,
            priority=priority,
            added_by="arch_ingest",
            notes=f"Igor self-architecture doc — {rel_path}",
        )
        if result.startswith("Error"):
            errors.append(f"{rel_path}: {result}")
        else:
            queued.append(result)

    summary = f"[ingest_arch_docs] queued={len(queued)} skipped={len(skipped)} errors={len(errors)}"
    try:
        from ..cognition.forensic_logger import log_anomaly as _la

        _la(
            kind="ARCH_INGEST_DONE",
            detail=summary + (f" | errors: {errors}" if errors else ""),
        )
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"learner.py:1196: {_exc}")

    return summary


registry.register(
    Tool(
        name="ingest_arch_docs",
        description="Queue Igor's own architecture design docs (decisions_log, capabilities_index, subsystem docs, etc.) into reading_list at high priority and encoding_arousal. Idempotent — safe to call multiple times.",
        parameters={"type": "object", "properties": {}},
        fn=ingest_arch_docs,
    )
)


# ── Self-directed gap flagging ──────────────────────────────────────────────────

_GAP_FLAG_SALIENCE_THRESHOLD = 0.7
_GAP_FLAG_COOLDOWN_SEC = 900  # don't re-flag the same question within 15 min
_gap_flag_last: dict[str, float] = {}  # question → last flagged timestamp


def flag_top_gap(**_kwargs) -> str:
    """
    If the highest-salience unexpired NARRATIVE_GAP exceeds the threshold, post
    an 'I noticed:' message to the channel so Akien sees Igor noticing things.
    Called by SchedulerSource via PROC_FLAG_ANOMALY every 5 min.
    Writes directly to channel_messages (Postgres) + messages.jsonl.
    """
    import json as _json
    import time as _time
    from datetime import datetime, timezone

    # Query highest-salience unexpired NARRATIVE_GAP
    try:
        with _local_db_proxy()() as conn:
            row = conn.execute(
                """SELECT content_csb, salience FROM twm_observations
                   WHERE content_csb LIKE %s
                     AND (expires_at IS NULL OR expires_at > to_char(NOW(), 'YYYY-MM-DD"T"HH24:MI:SS'))
                   ORDER BY salience DESC LIMIT 1""",
                ("NARRATIVE_GAP|%",),
            ).fetchone()
    except Exception as e:
        return f"[flag_top_gap] DB error: {e}"

    if not row:
        return "[flag_top_gap] no active gaps"

    content = row[0] if isinstance(row, (list, tuple)) else row["content_csb"]
    salience = float(row[1] if isinstance(row, (list, tuple)) else row["salience"])

    if salience < _GAP_FLAG_SALIENCE_THRESHOLD:
        return f"[flag_top_gap] top gap salience={salience:.2f} below threshold — quiet"

    # Parse question
    question = ""
    for part in content.split("|"):
        if part.startswith("question="):
            question = part[len("question=") :]
            break
    if not question:
        return f"[flag_top_gap] could not parse question from: {content[:80]}"

    # Suppress self-referential blockage gaps when standing goals (AGED_INTENT) are present.
    # These are not genuine causal unknowns — they're the NE signalling "I don't know
    # what to do" when idle standing goals are available. Broadcasting them to channel
    # creates a self-reinforcing loop. Defense layer: even if _process_gaps() lets one
    # through, don't shout it to the channel.
    _q_lower = question.lower()
    if (
        "cognitive" in _q_lower
        and any(w in _q_lower for w in ("blockage", "blockages", "blocking", "blocked"))
    ):
        try:
            _aged_check = _local_db_proxy()
            with _aged_check() as _ac:
                _aged_row = _ac.execute(
                    """SELECT 1 FROM twm_observations
                       WHERE content_csb LIKE %s
                         AND (expires_at IS NULL OR expires_at > to_char(NOW(), 'YYYY-MM-DD"T"HH24:MI:SS'))
                       LIMIT 1""",
                    ("AGED_INTENT|%",),
                ).fetchone()
            if _aged_row:
                return f"[flag_top_gap] suppressed: blockage gap with standing goals present — {question[:60]}"
        except Exception as _se:
            pass  # if check fails, fall through and flag normally

    # Cooldown — don't spam the same question
    now = _time.time()
    if (
        question in _gap_flag_last
        and now - _gap_flag_last[question] < _GAP_FLAG_COOLDOWN_SEC
    ):
        return f"[flag_top_gap] cooldown active for: {question[:60]}"
    _gap_flag_last[question] = now

    message = f"[Igor notices] {question} (salience={salience:.2f})"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write to Postgres channel_messages
    try:
        import os as _os
        import psycopg2 as _pg

        pg_url = _os.environ.get("IGOR_HOME_DB_URL", "") or _os.environ.get(
            "IGOR_DB_URL", ""
        )
        if pg_url:
            conn_pg = _pg.connect(pg_url)
            with conn_pg:
                with conn_pg.cursor() as c:
                    c.execute(
                        "INSERT INTO channel_messages"
                        " (ts, author, type, content, channel, source_agent)"
                        " VALUES (%s, %s, %s, %s, %s, %s)",
                        (ts, "igor", "message", message, "shared", "igor"),
                    )
            conn_pg.close()
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"learner.py:1290: {_exc}")

    # Write to JSONL channel file
    try:
        channel_file = paths().cc_channel / "messages.jsonl"
        channel_file.parent.mkdir(parents=True, exist_ok=True)
        entry = _json.dumps(
            {"ts": ts, "author": "igor", "type": "message", "content": message},
            ensure_ascii=False,
        )
        with open(channel_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"learner.py:1303: {_exc}")

    try:
        from ..cognition.forensic_logger import log_anomaly as _la

        _la(kind="GAP_FLAGGED", detail=f"salience={salience:.2f} q={question[:120]}")
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"learner.py:1310: {_exc}")

    return f"[flag_top_gap] flagged: {question[:80]}"


registry.register(
    Tool(
        name="flag_top_gap",
        description="Check if any high-salience NARRATIVE_GAP exists in TWM; if so, post '[Igor notices]: {question}' to the channel as author=igor. Called every 5 min by PROC_FLAG_ANOMALY.",
        parameters={"type": "object", "properties": {}},
        fn=flag_top_gap,
    )
)


# ── Nightly turn-trace self-review ─────────────────────────────────────────────


def _parse_turn_trace_logs(log_dirs: list, since_hours: int = 24) -> list[dict]:
    """
    Walk turn_trace.*.log files in log_dirs modified within since_hours.
    Return list of parsed turn dicts for cloud-escape turns
    (reasoning.tier contains 'cloud' AND habit_exec has no habit_id).
    """
    import glob as _glob
    import json as _json
    import re as _re
    import time as _time

    cutoff = _time.time() - since_hours * 3600
    escapes = []

    for log_dir in log_dirs:
        pattern = str(Path(log_dir) / "turn_trace.*.log")
        for fpath in _glob.glob(pattern):
            try:
                if Path(fpath).stat().st_mtime < cutoff:
                    continue
                text = Path(fpath).read_text(encoding="utf-8", errors="replace")
                parts = _re.split(r"(?=^=== turn )", text, flags=_re.MULTILINE)
                dec = _json.JSONDecoder()
                for part in parts:
                    if not part.strip():
                        continue
                    m = _re.match(r"^=== turn ([^\s]+)[^\n]+\n(.*)", part, _re.DOTALL)
                    if not m:
                        continue
                    turn_id = m.group(1)
                    body = m.group(2).strip()
                    try:
                        obj, _ = dec.raw_decode(body)
                    except Exception:
                        continue
                    reasoning_tier = obj.get("reasoning", {}).get("tier", "")
                    habit_id = obj.get("habit_exec", {}).get("habit_id", "")
                    if "cloud" in reasoning_tier and not habit_id:
                        escapes.append(
                            {
                                "turn_id": turn_id,
                                "ts": obj.get("ts", ""),
                                "input": obj.get("input", "")[:200],
                                "intent": obj.get("thalamus", {}).get("intent", ""),
                                "routing_tier": obj.get("routing", {}).get("tier", ""),
                                "cost_usd": obj.get("TOTAL", {}).get("cost_usd", 0.0),
                                "bg_winner": obj.get("bg_scoring", {}).get(
                                    "winner", ""
                                ),
                            }
                        )
            except Exception:
                continue

    return escapes


def review_turn_traces(**_kwargs) -> str:
    """
    Nightly self-review: scan recent turn traces for cloud escalations where no
    habit fired. Each unique escape is added to reading_list as book_type=cloud-escape-gap
    so Akien and Claude can review and build plugs. Deposits one summary NARRATIVE_GAP
    into twm_observations so PROC_FLAG_ANOMALY surfaces it.
    Called by SchedulerSource via PROC_TRACE_REVIEW once per day.
    """
    import time as _time
    from datetime import datetime, timezone

    log_dirs = [
        paths().runtime / "logs",  # ~/.unseen_university/logs/ (legacy)
        paths().logs,  # ~/.unseen_university/local/logs/ (current)
    ]
    escapes = _parse_turn_trace_logs(log_dirs, since_hours=24)
    if not escapes:
        return "[review_turn_traces] no cloud escapes in last 24h"

    # Fetch already-queued trace sources to avoid duplicates
    try:
        with _igor_db_proxy()() as conn:
            rows = conn.execute(
                "SELECT source FROM reading_list WHERE book_type = 'cloud-escape-gap'"
            ).fetchall()
        existing_sources = {r[0] for r in rows if r[0]}
    except Exception as e:
        return f"[review_turn_traces] DB error fetching existing: {e}"

    # D334: cloud escape traces are NOT books — don't pollute reading_list.
    # The TWM NARRATIVE_GAP deposit below is sufficient for surfacing these.
    # Previously, each escape was added to reading_list as book_type=cloud-escape-gap,
    # which created hundreds of junk entries that the book_learner then crashed on.
    queued = []
    for esc in escapes:
        source = f"trace://{esc['turn_id']}"
        if source in existing_sources:
            continue
        queued.append(esc["turn_id"])
        existing_sources.add(source)

    # Deposit a summary NARRATIVE_GAP into TWM so flag_top_gap can surface it
    if queued:
        summary_q = f"I escalated to cloud {len(queued)} time(s) recently without a habit firing — what plugs are missing?"
        try:
            ts_now = datetime.now(timezone.utc)
            expires = ts_now.strftime("%Y-%m-%dT%H:%M:%S")  # will be extended below
            from datetime import timedelta

            expires = (ts_now + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S")
            with _local_db_proxy()() as conn:
                conn.execute(
                    """INSERT INTO twm_observations
                       (content_csb, salience, expires_at, timestamp, source, urgency)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        f"NARRATIVE_GAP|question={summary_q}|salience=0.75|threat=0.1",
                        0.75,
                        expires,
                        ts_now.strftime("%Y-%m-%dT%H:%M:%S"),
                        "trace_review",
                        0.5,
                    ),
                )
        except Exception as _exc:
            from ..cognition.forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"learner.py:1450: {_exc}")

    summary = (
        f"[review_turn_traces] new_escapes={len(queued)} total_found={len(escapes)}"
    )
    try:
        from ..cognition.forensic_logger import log_anomaly as _la

        _la(kind="TRACE_REVIEW_DONE", detail=summary)
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"learner.py:1460: {_exc}")

    return summary


registry.register(
    Tool(
        name="review_turn_traces",
        description="Nightly self-review: scan turn trace logs for cloud escalations with no habit firing; deposit each as cloud-escape-gap in reading_list and post a summary NARRATIVE_GAP to TWM. Called daily by PROC_TRACE_REVIEW.",
        parameters={"type": "object", "properties": {}},
        fn=review_turn_traces,
    )
)


# ── Calibre Igor-tagged book ingest ────────────────────────────────────────────

_CALIBRE_DB = Path(
    "/home/akien/.TheIgors/akien/onedrive/AkiensMedia/Ebooks"
    "/Calibre Portable/Calibre Library/metadata.db"
)

# Tag sets for secondary-tier classification of Igor-tagged books.
# Checked in priority order — first match wins.
_CALIBRE_PROG_SUBJECTS = {
    "computers",
    "programming",
    "software",
    "computer science",
    "python",
    "javascript",
    "java",
    "c#",
    "c++",
    "ruby",
    "algorithms",
    "data structures",
    "machine learning",
    "artificial intelligence",
    "web development",
    "computer programming",
    "software engineering",
    "open source",
    "linux",
    "unix",
    "database",
    "electronics",
    "electrical",
    "engineering",
    "circuits",
}
_CALIBRE_PROG_TITLE_KW = {
    "python",
    "javascript",
    "java",
    "linux",
    "unix",
    "algorithm",
    "programming",
    "software",
    "coding",
    "developer",
    "kubernetes",
    "docker",
    "sql",
    "api",
    "devops",
    "agile",
    "c#",
    ".net",
    "wpf",
    "asp.net",
    "selenium",
    "playwright",
    "automation",
    "refactoring",
    "compiler",
    "debugger",
    "programmer",
    "electronics",
    "electrical",
    "circuits",
    "engineering",
}
_CALIBRE_NEURO_TAGS = {
    "neurology",
    "neuroscience",
    "neuropsychology",
    "neural networks",
    "neural networks (computer science)",
    "brain",
    "consciousness",
    "physiological psychology",
    "human anatomy & physiology",
    "life sciences",
    "cognitive neuroscience",
}
_CALIBRE_PSYCH_TAGS = {
    "psychology",
    "applied psychology",
    "cognitive psychology",
    "conflict (psychology)",
    "ego (psychology)",
    "pscyhology",
    "pyschology",
    "psychological aspects",
    "physiological aspects",
    "emotions",
    "time - psychological aspects",
    "personality",
    "psychiatry",
    "consciousness - physiological aspects",
    "emotions - physiological aspects",
}
_CALIBRE_FICTION_TAGS = {
    "fiction",
    "fantasy fiction",
    "visionary fiction",
    "literary",
    "fantasy",
}
_CALIBRE_CULTURE_TAGS = {
    "culture",
    "history",
    "social science",
    "sociology",
    "civilization",
    "anthropology",
    "social history",
    "world",
    "history & surveys",
}
_CALIBRE_HEALTH_TAGS = {
    "health",
    "health & fitness",
    "health and hygiene",
    "men's health",
    "medical",
    "mind & body",
    "mental health",
    "exercise",
    "men - health and hygiene",
}


def _calibre_classify(tags: set, title: str) -> tuple:
    """Return (arousal, tier_name) for a calibre-igor book.

    Tier order (highest first):
      akien → computers → neurology → psychology → culture → other → fiction → health
    """
    t = tags  # already lowercased by caller
    title_words = set(title.lower().split())

    if "akien" in t:
        return 0.85, "akien"
    if t & _CALIBRE_PROG_SUBJECTS or title_words & _CALIBRE_PROG_TITLE_KW:
        return 0.65, "computers"
    if t & _CALIBRE_NEURO_TAGS:
        return 0.62, "neurology"
    if t & _CALIBRE_PSYCH_TAGS:
        return 0.55, "psychology"
    if t & _CALIBRE_CULTURE_TAGS:
        return 0.25, "culture"
    if t & _CALIBRE_FICTION_TAGS:
        return 0.18, "fiction"
    if t & _CALIBRE_HEALTH_TAGS:
        return 0.12, "health"
    return 0.20, "other"


def ingest_calibre_igor_books(**_kwargs) -> str:
    """Scan Calibre for books tagged 'Igor', insert new ones and update mis-tiered ones.

    Tier map (arousal): akien=0.85, computers=0.65, neurology=0.62,
      psychology=0.55, culture=0.25, other=0.20, fiction=0.18, health=0.12.
    Igor tag overrides SKIP categories — manual curation wins.
    Idempotent by calibre://{id} source URL; re-run after adding Calibre tags.
    Called daily by PROC_CALIBRE_INGEST.
    """
    from devices.igor._sqlite_guard import real_sqlite3 as sqlite3

    if not _CALIBRE_DB.exists():
        return f"[ingest_calibre_igor_books] Calibre DB not found: {_CALIBRE_DB}"

    # ── Load Igor-tagged books from Calibre ────────────────────────────────────
    try:
        cal = sqlite3.connect(str(_CALIBRE_DB))
        cal.row_factory = sqlite3.Row
        cur = cal.cursor()

        cur.execute("""
            SELECT DISTINCT b.id, b.title, a.name AS author
            FROM books b
            JOIN books_tags_link btl ON b.id = btl.book
            JOIN tags t ON btl.tag = t.id AND lower(t.name) = 'igor'
            LEFT JOIN books_authors_link bal ON b.id = bal.book
            LEFT JOIN authors a ON bal.author = a.id
            ORDER BY b.title
        """)
        igor_books = [dict(r) for r in cur.fetchall()]

        book_ids = [b["id"] for b in igor_books]
        tags_by_id: dict = {}
        if book_ids:
            placeholders = ",".join("?" * len(book_ids))
            cur.execute(
                f"SELECT btl.book, t.name FROM books_tags_link btl "
                f"JOIN tags t ON btl.tag = t.id WHERE btl.book IN ({placeholders})",
                book_ids,
            )
            for row in cur.fetchall():
                tags_by_id.setdefault(row[0], set()).add(row[1].lower())
        cal.close()
    except Exception as e:
        return f"[ingest_calibre_igor_books] Calibre read error: {e}"

    # ── Fetch existing calibre:// entries with their current arousal ───────────
    try:
        with _igor_db_proxy()() as conn:
            rows = conn.execute(
                "SELECT source, encoding_arousal FROM reading_list WHERE source LIKE %s",
                ("calibre://%",),
            ).fetchall()
        existing: dict = {r[0]: float(r[1]) for r in rows if r[0]}
    except Exception as e:
        return f"[ingest_calibre_igor_books] DB error fetching existing: {e}"

    # ── Seed per-tier priority counters from current DB maxima ────────────────
    _tier_ranges = {
        "akien": (15, 29),
        "computers": (210, 239),
        "neurology": (240, 269),
        "psychology": (320, 359),
        "culture": (400, 449),
        "other": (600, 649),
        "fiction": (650, 699),
        "health": (700, 749),
    }
    pri_counters: dict = {}
    try:
        with _igor_db_proxy()() as conn:
            for tier, (lo, hi) in _tier_ranges.items():
                r = conn.execute(
                    "SELECT MAX(priority) FROM reading_list WHERE priority BETWEEN %s AND %s",
                    (lo, hi),
                ).fetchone()
                pri_counters[tier] = (r[0] or lo - 1) + 1
    except Exception:
        pri_counters = {t: lo for t, (lo, _) in _tier_ranges.items()}

    # ── Classify and insert/update ────────────────────────────────────────────
    inserted: dict = {}  # tier → count
    updated: dict = {}
    skipped = 0

    for book in igor_books:
        source = f"calibre://{book['id']}"
        tags = tags_by_id.get(book["id"], set())
        arousal, tier = _calibre_classify(tags, book["title"])
        notes = f"tier={tier} tags={','.join(sorted(tags))[:70]}"

        if source not in existing:
            priority = pri_counters.get(tier, 600)
            pri_counters[tier] = priority + 1
            add_to_reading_list(
                title=book["title"],
                author=book["author"] or "Unknown",
                source=source,
                book_type="calibre-igor",
                encoding_arousal=arousal,
                priority=priority,
                added_by="igor_self",
                notes=notes,
            )
            inserted[tier] = inserted.get(tier, 0) + 1
        elif abs(existing[source] - arousal) > 0.001:
            # Arousal changed (re-tiered) — update in place
            try:
                with _igor_db_proxy()() as conn:
                    conn.execute(
                        "UPDATE reading_list SET encoding_arousal=%s, notes=%s WHERE source=%s",
                        (arousal, notes, source),
                    )
                updated[tier] = updated.get(tier, 0) + 1
            except Exception as _exc:
                from ..cognition.forensic_logger import log_error as _le

                _le(kind="SILENT_EXCEPT", detail=f"learner.py:1746: {_exc}")
        else:
            skipped += 1

    total_in = sum(inserted.values())
    total_up = sum(updated.values())
    summary = (
        f"[ingest_calibre_igor_books] inserted={total_in} updated={total_up} "
        f"skipped={skipped} tiers={inserted or updated}"
    )
    try:
        from ..cognition.forensic_logger import log_anomaly as _la

        _la(kind="CALIBRE_INGEST_DONE", detail=summary)
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"learner.py:1761: {_exc}")

    return summary


registry.register(
    Tool(
        name="ingest_calibre_igor_books",
        description="Scan Calibre for books tagged 'Igor' and insert/update reading_list entries. Tiers by secondary tag: akien=0.85, computers=0.65, neurology=0.62, psychology=0.55, culture=0.25, other=0.20, fiction=0.18, health=0.12. Idempotent — re-run after Akien tags more books in Calibre. Called daily by PROC_CALIBRE_INGEST.",
        parameters={"type": "object", "properties": {}},
        fn=ingest_calibre_igor_books,
    )
)


# ── Readings design-session ingest (T-readings-ingest) ───────────────────────

_READINGS_DIR = Path.home() / "TheIgorsProject" / "akien" / "Readings"
_READINGS_EXTS = {".txt", ".md", ".rst"}
# Files to skip (meta files, not design sessions)
_READINGS_SKIP_PATTERNS = {"learn_queue", "README", ".gitignore"}


def ingest_readings_design_sessions(**_kwargs) -> str:
    """
    Queue TheIgorsProject/akien/Readings/ text files into reading_list
    as high-arousal design-session documents.

    These are the early Akien+Claude/Gemini/Igor conversations that shaped
    Igor's architecture — foundational context for Igor's self-model.

    Idempotent: skips files already present by source URL (file://).
    book_type='design-session', encoding_arousal=0.90, priority=1.
    """
    if not _READINGS_DIR.exists():
        return f"[ingest_readings] SKIP — {_READINGS_DIR} not found"

    queued: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    try:
        with _igor_db_proxy()() as conn:
            rows = conn.execute(
                "SELECT source FROM reading_list WHERE book_type = 'design-session'"
            ).fetchall()
        existing_sources = {r[0] for r in rows if r[0]}
    except Exception as e:
        return f"[ingest_readings] DB error fetching existing: {e}"

    for path in sorted(_READINGS_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _READINGS_EXTS:
            continue
        if any(pat in path.stem for pat in _READINGS_SKIP_PATTERNS):
            continue

        source_url = f"file://{path.resolve()}"
        if source_url in existing_sources:
            skipped.append(path.name)
            continue

        # Parse date from filename prefix (YYYYMMDD or YYYYMMDDHHMMSS)
        import re as _re

        date_m = _re.match(r"^(\d{8})", path.stem)
        date_prefix = date_m.group(1) if date_m else ""
        raw_title = (
            _re.sub(r"^\d{8,14}[._]?", "", path.stem)
            .replace("_", " ")
            .replace(".", " ")
            .strip()
        )
        title = (
            f"{date_prefix[:4]}-{date_prefix[4:6]}-{date_prefix[6:8]} {raw_title}".strip(
                " -"
            )
            if date_prefix
            else raw_title
        ) or path.stem

        result = add_to_reading_list(
            title=title,
            source=source_url,
            book_type="design-session",
            encoding_arousal=0.90,
            priority=1,
            added_by="readings_ingest",
            notes=f"Early design session — {path.name}",
        )
        if isinstance(result, str) and result.startswith("Error"):
            errors.append(f"{path.name}: {result}")
        else:
            queued.append(path.name)

    summary = f"[ingest_readings] queued={len(queued)} skipped={len(skipped)} errors={len(errors)}"
    try:
        from ..cognition.forensic_logger import log_anomaly as _la

        _la(kind="READINGS_INGEST_DONE", detail=summary)
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"learner.py:1863: {_exc}")

    return summary


registry.register(
    Tool(
        name="ingest_readings_design_sessions",
        description=(
            "Queue TheIgorsProject/akien/Readings/ design-session transcripts into "
            "reading_list. These are foundational Akien+AI conversations that shaped "
            "Igor's architecture. High encoding_arousal=0.90, priority=1. "
            "Idempotent — safe to re-run after new files are added."
        ),
        parameters={"type": "object", "properties": {}},
        fn=ingest_readings_design_sessions,
    )
)


# ── Reading list → learn_queue feeder ─────────────────────────────────────────

_FEEDER_BATCH = 20  # items to move into learn_queue.json per run


def feed_reading_list(**_kwargs) -> str:
    """Pull the top-priority pending items from reading_list into learn_queue.json.

    Reads reading_list WHERE status = 'pending' ORDER BY
    encoding_arousal DESC, priority ASC LIMIT _FEEDER_BATCH, converts each
    to a learn_queue.json entry (url=source, title, cloud_ok=True for URLs /
    False for calibre:// so drain runner uses local inference for ebooks),
    deduplicates against existing queue by URL, appends new entries, then
    spawns drain_learn_queue.py if not already running.

    Called hourly by PROC_READING_FEEDER.
    """
    # ── Atomically claim top items from reading_list ────────────────────────
    # D333: two instances share the same DB. Use UPDATE ... RETURNING to
    # atomically claim books so no two instances can grab the same one.
    try:
        with _igor_db_proxy()() as conn:
            rows = conn.execute(
                """UPDATE reading_list
                   SET status = 'queued'
                   WHERE source IN (
                       SELECT source FROM reading_list
                       WHERE status = 'pending' AND run_id IS NULL AND source IS NOT NULL AND source != ''
                       ORDER BY encoding_arousal DESC, priority ASC
                       LIMIT %s
                   )
                   RETURNING source, title, author, encoding_arousal""",
                (_FEEDER_BATCH,),
            ).fetchall()
    except Exception as e:
        return f"[feed_reading_list] DB error: {e}"

    if not rows:
        return "[feed_reading_list] reading_list is empty — nothing to feed"

    # ── Load existing queue, dedup by url ─────────────────────────────────────
    existing_q = _load_queue()
    done_urls = {e.get("url") for e in existing_q if e.get("done")}
    existing_urls = {e.get("url") for e in existing_q if not e.get("done")}
    pending_in_q = len(existing_urls)

    # Sync: mark reading_list entries completed for drain-runner-finished items,
    # then trim done entries from the queue so the file doesn't grow unbounded
    # and so the next feeder run can load new items.
    if done_urls:
        try:
            with _igor_db_proxy()() as conn:
                for url in done_urls:
                    conn.execute(
                        "UPDATE reading_list SET status='completed', completed_at=%s"
                        " WHERE source=%s AND status NOT IN ('completed', 'paused')",
                        (datetime.now().isoformat(), url),
                    )
        except Exception as _e:
            from ..cognition.forensic_logger import log_error as _le

            _le(kind="SILENT_EXCEPT", detail=f"learner.py:1942: {_e}")
        existing_q = [e for e in existing_q if not e.get("done")]
        _save_queue(existing_q)

    added = 0
    for row in rows:
        source = row[0]
        title = row[1] or source
        author = row[2] or ""

        if source in existing_urls:
            continue

        # calibre:// sources → local inference (no cloud spend for bulk ebooks)
        cloud_ok = not source.startswith("calibre://")

        label = f"{title} — {author}".strip(" —") if author else title
        existing_q.append(
            {
                "url": source,
                "title": label[:100],
                "cloud_ok": cloud_ok,
                "done": False,
            }
        )
        existing_urls.add(source)
        added += 1

    if added:
        _save_queue(existing_q)

    # ── Start drain runner if not already running ──────────────────────────────
    launched = False
    if pending_in_q + added > 0:
        launched = _launch_queue_runner()

    summary = (
        f"[feed_reading_list] added={added} already_queued={pending_in_q} "
        f"drain_launched={launched}"
    )
    try:
        from ..cognition.forensic_logger import log_anomaly as _la

        _la(kind="READING_FEEDER_RUN", detail=summary)
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"learner.py:1987: {_exc}")

    return summary


registry.register(
    Tool(
        name="feed_reading_list",
        description="Pull top-priority items from reading_list (by encoding_arousal DESC) into learn_queue.json and start the drain runner. Feeds up to 20 items per call. Called hourly by PROC_READING_FEEDER. Safe to call manually to kick off reading.",
        parameters={"type": "object", "properties": {}},
        fn=feed_reading_list,
    )
)


registry.register(
    Tool(
        name="annotate_learning",
        description=(
            "Record a personal experience — whether an approach, technique, or strategy "
            "worked or didn't work for Akien. Deposits a high-confidence EXPERIENTIAL "
            "memory. Use when Akien says 'this worked', 'that didn't work', 'mark that', etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "description": "What approach/technique/strategy to record.",
                },
                "worked": {
                    "type": "boolean",
                    "description": "True if it worked, False if it didn't.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional additional context.",
                },
            },
            "required": ["outcome"],
        },
        fn=annotate_learning,
    )
)

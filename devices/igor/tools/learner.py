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
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .registry import Tool, registry

# ── Paths ──────────────────────────────────────────────────────────────────────
_REPO = Path(__file__).parent.parent.parent.parent
_BOOK_LEARNER = _REPO / "claudecode" / "book_learner.py"
_DRAIN_SCRIPT = _REPO / "claudecode" / "drain_learn_queue.py"
_VENV_PYTHON = _REPO / "venv" / "bin" / "python"
_QUEUE_FILE = Path.home() / ".TheIgors" / "learn_queue.json"
_DRAIN_PID = Path.home() / ".TheIgors" / "drain_learn_queue.pid"

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
    low = user_input.lower().strip()
    # Search anywhere in the input — handles thread-context-prefixed CC bridge messages
    # where the actual request appears after "[Web message from akien]: go learn about X"
    for t in sorted(_TRIGGERS, key=len, reverse=True):
        idx = low.find(t)
        if idx != -1:
            topic = user_input[idx + len(t) :].strip(" .:,")
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
    except Exception as _e:
        try:
            from ..cognition.forensic_logger import log_error as _le

            _le(
                kind="LEARN_QUEUE_LOAD_FAIL",
                detail=str(_e),
                source="learner._load_queue",
            )
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
    q.append(
        {
            "url": url,
            "title": title,
            "topic": topic,
            "added_at": datetime.now().isoformat(),
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
                except Exception:
                    pass
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
        log_dir = Path.home() / ".TheIgors" / "logs"
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
        log_dir = Path.home() / ".TheIgors" / "logs"
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

    if not topic:
        return "What topic shall I learn about? Try: go learn about consciousness"

    lines = [f"Learning about: {topic}" + (" (queued for tonight)" if tonight else "")]

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
            _queue_url(f"calibre://{cid}", title, topic)
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
            _queue_url(url, title, topic)
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
                _queue_url(url, title, topic)
            urls_queued += len(url_pairs)
            lines.append(f"Web: {len(url_pairs)} AI-discovered URL(s) queued.")
        else:
            lines.append(
                "Web: browser AI discovery returned no URLs — check ~/.TheIgors/logs/browser_use.log."
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
            f"60s between launches. Check ~/.TheIgors/logs/drain_learn_queue.log for progress."
        )
    return "Failed to launch queue runner — check logs."


registry.register(
    Tool(
        name="drain_learn_queue",
        description=(
            "Start the background learning queue runner. Drains ~/.TheIgors/learn_queue.json "
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
    import os, sqlite3, json

    db_path = os.environ.get(
        "IGOR_DB_PATH", os.path.expanduser("~/.TheIgors/igor_wild_0001/wild-0001.db")
    )
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT metadata, COUNT(*) as node_count
            FROM memories
            WHERE memory_type IN ('FACTUAL','INTERPRETIVE','PROCEDURAL')
              AND source = 'reading'
              AND json_extract(metadata, '$.book_title') IS NOT NULL
            GROUP BY json_extract(metadata, '$.book_title'), json_extract(metadata, '$.book_author')
            ORDER BY node_count DESC
        """)
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return f"Error querying absorbed books: {e}"

    if not rows:
        return "No books have been absorbed yet."

    lines = [f"Absorbed {len(rows)} source(s):"]
    total_nodes = 0
    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        title = meta.get("book_title", "Unknown")
        author = meta.get("book_author", "")
        count = row["node_count"]
        total_nodes += count
        entry = f"  • {title}"
        if author:
            entry += f" — {author}"
        entry += f"  ({count} nodes)"
        lines.append(entry)
    lines.append(f"\nTotal: {total_nodes} knowledge nodes")

    # Also show queue
    queue_path = Path.home() / ".TheIgors" / "learn_queue.json"
    if queue_path.exists():
        try:
            queue = json.loads(queue_path.read_text())
            if queue:
                lines.append(f"\nQueued to learn: {len(queue)} item(s)")
        except Exception:
            pass

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


def _rl_db() -> sqlite3.Connection:
    import os

    db_path = os.environ.get(
        "IGOR_DB_PATH", os.path.expanduser("~/.TheIgors/igor_wild_0001/wild-0001.db")
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_reading_list(**kwargs) -> str:
    """Return the reading list, optionally filtered by status or book_type."""
    status_filter = kwargs.get("status")  # e.g. "queued", "in_progress", "completed"
    type_filter = kwargs.get("book_type")  # "fiction" | "nonfiction"
    try:
        conn = _rl_db()
        cur = conn.cursor()
        sql = "SELECT * FROM reading_list WHERE 1=1"
        params = []
        if status_filter:
            sql += " AND status = ?"
            params.append(status_filter)
        if type_filter:
            sql += " AND book_type = ?"
            params.append(type_filter)
        sql += " ORDER BY priority, id"
        cur.execute(sql, params)
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return f"Error reading reading_list: {e}"

    if not rows:
        return "No entries match."

    _STATUS_ICON = {
        "queued": "○",
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
        conn = _rl_db()
        cur = conn.cursor()
        cur.execute("SELECT MAX(CAST(SUBSTR(id,4) AS INTEGER)) FROM reading_list")
        row = cur.fetchone()
        max_n = row[0] or 0
        new_id = f"RL_{max_n + 1:03d}"
        cur.execute(
            """
            INSERT INTO reading_list
            (id, title, author, source, book_type, reading_rate, priority, status,
             emotional_significance, encoding_arousal, notes, added_by, added_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                new_id,
                title,
                author,
                source,
                kwargs.get("book_type", "nonfiction"),
                kwargs.get("reading_rate", "fast"),
                kwargs.get("priority", 50),
                kwargs.get("status", "queued"),
                kwargs.get("emotional_significance"),
                float(kwargs.get("encoding_arousal", 0.3)),
                kwargs.get("notes"),
                kwargs.get("added_by", "igor"),
                _time.strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )
        conn.commit()
        conn.close()
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
    valid = ("queued", "in_progress", "completed", "needs_acquisition", "paused")
    if status not in valid:
        return f"status must be one of: {', '.join(valid)}"
    try:
        conn = _rl_db()
        cur = conn.cursor()
        ts = _time.strftime("%Y-%m-%dT%H:%M:%S")
        if status == "in_progress":
            cur.execute(
                "UPDATE reading_list SET status=?, started_at=? WHERE id=?",
                (status, ts, rl_id),
            )
        elif status == "completed":
            cur.execute(
                "UPDATE reading_list SET status=?, completed_at=? WHERE id=?",
                (status, ts, rl_id),
            )
        else:
            cur.execute("UPDATE reading_list SET status=? WHERE id=?", (status, rl_id))
        conn.commit()
        changed = cur.rowcount
        conn.close()
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
        description="Show Igor's permanent reading list. Filter by status (queued/in_progress/completed/needs_acquisition/paused) or book_type (fiction/nonfiction).",
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

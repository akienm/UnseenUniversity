"""
Training corpus tools — WO#138 / D038.

Igor can use these to:
  - Fetch books from Gutenberg (or any URL) into the staged corpus
  - Load Akien's URL lists from ~/TheIgorsProject/
  - Scan ~/TheIgorsProject/ for local .txt training files
  - Train the word graph on staged books
  - Monitor corpus status and evict when disk is tight

Training takes effect on next restart (word graph reloaded from cache at boot).
Disk governor: eviction is automatic before fetch and after each training run.
"""

import os
from pathlib import Path
from .registry import Tool, registry


def _wg_save_path() -> Path:
    from ..cognition.word_graph import default_cache_path
    return default_cache_path()


def _load_wg() -> "WordGraph":  # type: ignore[name-defined]  # noqa: F821
    from ..cognition.word_graph import WordGraph, default_cache_path
    return WordGraph.load(default_cache_path())


# ── Tools ──────────────────────────────────────────────────────────────────────

def _fetch_training_text(url: str, title: str = "", source: str = "gutenberg", **_) -> str:
    """Fetch a book from a URL into the training corpus."""
    from ..cognition.training_corpus import fetch
    if not title:
        # Use the last path segment as a fallback title
        title = url.rstrip("/").split("/")[-1].replace(".txt", "").replace("-", " ")
    book_id, msg = fetch(url, title, source)
    return msg


def _scan_local_training_source(**_) -> str:
    """
    Scan ~/TheIgorsProject/ (or IGOR_TRAINING_SOURCE_DIR) for .txt training files
    and register any not already in the corpus.
    """
    from ..cognition.training_corpus import scan_local
    return scan_local()


def _load_gutenberg_list(list_file: str = "", **_) -> str:
    """
    Read a URL-per-line file (e.g. Akien's Gutenberg reading list) and queue
    all books for fetching. Skips URLs already in corpus.
    Defaults to ~/TheIgorsProject/akien/Readings/gutenberg.org_top_100_vocabulary_expanding_books.txt
    """
    from ..cognition.training_corpus import load_url_list, fetch, _load_index

    if not list_file:
        list_file = str(
            Path.home() / "TheIgorsProject" / "akien" / "Readings"
            / "gutenberg.org_top_100_vocabulary_expanding_books.txt"
        )

    urls = load_url_list(list_file)
    if not urls:
        return f"No URLs found in: {list_file}"

    index    = _load_index()
    queued   = []
    skipped  = []
    errors   = []

    for url in urls:
        from ..cognition.training_corpus import _book_id
        bid = _book_id(url)
        if bid in index:
            skipped.append(url)
            continue
        title = url.rstrip("/").split("/")[-1].replace(".txt", "")
        book_id, msg = fetch(url, title, source="gutenberg")
        if book_id:
            queued.append(title)
        else:
            errors.append(f"{title}: {msg}")
        # Fetch one at a time; stop if disk critically low
        from ..cognition.training_corpus import _disk_free_gb
        if _disk_free_gb() < 0.2:
            errors.append("Disk critically low — stopping early.")
            break

    lines = [f"Gutenberg list: {len(urls)} URLs in {list_file}"]
    if queued:
        lines.append(f"  Queued {len(queued)}: {', '.join(queued[:5])}" + (" ..." if len(queued) > 5 else ""))
    if skipped:
        lines.append(f"  Skipped {len(skipped)} (already in corpus)")
    if errors:
        lines.append(f"  Errors ({len(errors)}): {'; '.join(errors[:3])}")
    return "\n".join(lines)


def _train_word_graph(book_id: str = "", **_) -> str:
    """
    Train the word graph on a corpus book.
    If book_id is empty, trains the next pending book.
    Training takes effect on next Igor restart.
    """
    from ..cognition.training_corpus import train, _load_index
    index = _load_index()

    if not book_id:
        # Find first pending
        for bid, meta in index.items():
            if meta["status"] == "pending":
                book_id = bid
                break
        if not book_id:
            # Try first in_progress (resume)
            for bid, meta in index.items():
                if meta["status"] == "in_progress":
                    book_id = bid
                    break
        if not book_id:
            return "No pending or in-progress books in corpus. Fetch some first."

    wg       = _load_wg()
    save_path = _wg_save_path()
    return train(book_id, wg, save_path)


def _train_all_pending(**_) -> str:
    """
    Train the word graph on all pending books in the corpus, one at a time.
    Stops if disk drops below threshold. Returns a summary.
    """
    from ..cognition.training_corpus import train, _load_index, _disk_free_gb

    index = _load_index()
    pending = [
        (bid, meta) for bid, meta in index.items()
        if meta["status"] in ("pending", "in_progress")
    ]
    if not pending:
        return "No pending books to train."

    wg        = _load_wg()
    save_path = _wg_save_path()
    results   = []

    for book_id, meta in pending:
        if _disk_free_gb() < 0.2:
            results.append("Disk critically low — stopping.")
            break
        msg = train(book_id, wg, save_path)
        results.append(msg)

    return "\n".join(results)


def _list_training_corpus(**_) -> str:
    """Show all books in the training corpus with their status."""
    from ..cognition.training_corpus import list_books
    return list_books()


def _evict_training_corpus(**_) -> str:
    """Manually trigger corpus eviction (complete → in_progress → pending)."""
    from ..cognition.training_corpus import evict
    return evict()


# ── Register ───────────────────────────────────────────────────────────────────

registry.register(Tool(
    name="fetch_training_text",
    description=(
        "Fetch a book or text from a URL into the training corpus (staged storage). "
        "Source: 'gutenberg' (default) or any label. "
        "Checks disk space before fetching; evicts completed books if needed. "
        "Training takes effect on next restart."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url":    {"type": "string", "description": "Direct URL to plain text file"},
            "title":  {"type": "string", "description": "Human-readable title (auto-derived from URL if omitted)"},
            "source": {"type": "string", "description": "Source label: 'gutenberg', 'local', etc. Default: 'gutenberg'"},
        },
        "required": ["url"],
    },
    fn=_fetch_training_text,
))

registry.register(Tool(
    name="scan_local_training_source",
    description=(
        "Scan ~/TheIgorsProject/ (or IGOR_TRAINING_SOURCE_DIR) for .txt files "
        "and register any not already in the training corpus. "
        "Use this to ingest Akien's own writings and notes."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    fn=_scan_local_training_source,
))

registry.register(Tool(
    name="load_gutenberg_list",
    description=(
        "Read Akien's Gutenberg URL list file and queue all books for fetching. "
        "Default file: ~/TheIgorsProject/akien/Readings/gutenberg.org_top_100_vocabulary_expanding_books.txt. "
        "Skips URLs already in corpus. Stops if disk is critically low."
    ),
    parameters={
        "type": "object",
        "properties": {
            "list_file": {
                "type": "string",
                "description": "Path to URL-per-line file. Defaults to the Gutenberg top-100 list.",
            },
        },
        "required": [],
    },
    fn=_load_gutenberg_list,
))

registry.register(Tool(
    name="train_word_graph",
    description=(
        "Train the word graph on a corpus book. "
        "If book_id is empty, trains the next pending book. "
        "Paragraphs are indexed; cursor saved for resumability. "
        "Takes effect on next Igor restart."
    ),
    parameters={
        "type": "object",
        "properties": {
            "book_id": {
                "type": "string",
                "description": "Corpus book ID (from list_training_corpus). Empty = next pending.",
            },
        },
        "required": [],
    },
    fn=_train_word_graph,
))

registry.register(Tool(
    name="train_all_pending",
    description=(
        "Train the word graph on ALL pending/in-progress corpus books. "
        "Processes one at a time; stops if disk drops critically low. "
        "Returns a per-book summary. Takes effect on next restart."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    fn=_train_all_pending,
))

registry.register(Tool(
    name="list_training_corpus",
    description=(
        "Show all books in the training corpus with status "
        "(pending / in_progress / complete), size, and training cursor. "
        "Also shows disk free space and local source dir status."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    fn=_list_training_corpus,
))

registry.register(Tool(
    name="evict_training_corpus",
    description=(
        "Manually evict training corpus files to free disk space. "
        "Priority: complete first, then in_progress (cursor saved), then pending. "
        "Stops when disk free >= IGOR_DISK_WARN_GB (default 1 GB)."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    fn=_evict_training_corpus,
))

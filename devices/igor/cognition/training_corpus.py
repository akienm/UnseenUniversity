"""
Training Corpus Manager — WO#138 / D038.

Manages a staged training corpus for the word graph:
  - Fetch text from URLs (Gutenberg, other sources) or scan local directory
  - Store temporarily in ~/.TheIgors/training_corpus/ while training
  - Evict when complete; evict early by priority if disk is tight
  - Track progress via a cursor so training can resume after interruption

Storage layout:
    ~/.TheIgors/training_corpus/
        index.json          — registry: {book_id: metadata}
        {book_id}.txt       — raw text (deleted after training unless keep=True)

Book status lifecycle:  pending → in_progress → complete → (evicted)

Eviction priority when disk is tight:
    1. complete    — training done, no value in keeping
    2. in_progress — checkpoint cursor, can re-fetch and resume
    3. pending     — never trained, cheapest to re-fetch

Local source directory (for Akien's own materials):
    Default: ~/TheIgorProject  (env: IGOR_TRAINING_SOURCE_DIR)
    scan_local() registers all .txt files found there.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .word_graph import WordGraph

CORPUS_DIR   = Path.home() / ".TheIgors" / "training_corpus"
INDEX_FILE   = CORPUS_DIR / "index.json"

# Paragraphs < this many chars are skipped (headers, short lines)
MIN_PARA_CHARS   = 60
# Max paragraphs indexed per book (cap word_graph.json growth)
MAX_PARAS_PER_BOOK = int(os.getenv("IGOR_TRAINING_MAX_PARAS", "800"))
# Disk free threshold below which eviction triggers (GB)
EVICT_THRESHOLD_GB = float(os.getenv("IGOR_DISK_WARN_GB", "1.0"))


def _book_id(url_or_path: str) -> str:
    """Stable 10-char ID from URL or path."""
    return hashlib.sha256(url_or_path.encode()).hexdigest()[:10]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _disk_free_gb() -> float:
    usage = shutil.disk_usage(str(CORPUS_DIR.parent))
    return usage.free / (1024 ** 3)


def _load_index() -> dict:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_index(index: dict) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, indent=2), encoding="utf-8")


# ── Fetch ──────────────────────────────────────────────────────────────────────

def fetch(url: str, title: str, source: str = "gutenberg") -> tuple[str, str]:
    """
    Download text from URL and register in corpus.
    Returns (book_id, message).
    Does NOT train — status set to 'pending'.
    """
    import requests, certifi

    book_id = _book_id(url)
    index   = _load_index()

    if book_id in index:
        existing = index[book_id]
        return book_id, (
            f"Already in corpus: '{existing['title']}' "
            f"(status={existing['status']}, id={book_id})"
        )

    # Check disk before fetching
    free_gb = _disk_free_gb()
    if free_gb < EVICT_THRESHOLD_GB:
        evict_msg = evict()
        free_gb = _disk_free_gb()
        if free_gb < 0.2:
            return "", f"Disk critically low ({free_gb:.2f} GB free) even after eviction. Aborting fetch."

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; IgorWordGraphTrainer/1.0)"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=30, verify=certifi.where())
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        return "", f"Fetch failed: {e}"

    text_path = CORPUS_DIR / f"{book_id}.txt"
    text_path.write_text(text, encoding="utf-8", errors="replace")

    index[book_id] = {
        "title":        title,
        "url":          url,
        "source":       source,
        "status":       "pending",
        "fetch_ts":     _now(),
        "train_ts":     None,
        "size_bytes":   len(text.encode("utf-8")),
        "para_cursor":  0,
    }
    _save_index(index)
    return book_id, (
        f"Fetched '{title}' ({len(text):,} chars) → id={book_id}, status=pending. "
        f"Disk free: {_disk_free_gb():.2f} GB."
    )


# ── Local source ───────────────────────────────────────────────────────────────

def local_source_dir() -> Path:
    return Path(os.getenv("IGOR_TRAINING_SOURCE_DIR",
                          str(Path.home() / "TheIgorsProject")))


def scan_local() -> str:
    """
    Scan IGOR_TRAINING_SOURCE_DIR (default ~/TheIgorProject) for .txt files
    and register any not already in the corpus (status=pending).
    Returns a summary.
    """
    src = local_source_dir()
    if not src.exists():
        return (
            f"Local source directory not found: {src}\n"
            "Create it and put .txt files there, or set IGOR_TRAINING_SOURCE_DIR."
        )
    files = sorted(src.rglob("*.txt"))
    if not files:
        return f"No .txt files found in {src}"

    index   = _load_index()
    added   = []
    already = []
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    for fpath in files:
        book_id = _book_id(str(fpath))
        if book_id in index:
            already.append(fpath.name)
            continue
        text      = fpath.read_text(encoding="utf-8", errors="replace")
        text_path = CORPUS_DIR / f"{book_id}.txt"
        # Symlink if in same filesystem, else copy
        try:
            text_path.symlink_to(fpath.resolve())
        except Exception:
            text_path.write_text(text, encoding="utf-8")
        index[book_id] = {
            "title":        fpath.stem,
            "url":          str(fpath.resolve()),
            "source":       "local",
            "status":       "pending",
            "fetch_ts":     _now(),
            "train_ts":     None,
            "size_bytes":   fpath.stat().st_size,
            "para_cursor":  0,
        }
        added.append(fpath.name)

    _save_index(index)
    lines = [f"Scanned {src}:"]
    if added:
        lines.append(f"  Registered {len(added)} new file(s): {', '.join(added[:5])}"
                     + (" ..." if len(added) > 5 else ""))
    if already:
        lines.append(f"  Already in corpus: {len(already)} file(s)")
    if not added and not already:
        lines.append("  No files to add.")
    return "\n".join(lines)


# ── Train ──────────────────────────────────────────────────────────────────────

def train(book_id: str, word_graph: "WordGraph", wg_save_path: Path) -> str:
    """
    Train word_graph on the book identified by book_id.
    Splits into paragraphs; respects para_cursor for resumability.
    Saves word_graph after completion.
    Returns a status message.
    """
    index = _load_index()
    if book_id not in index:
        return f"Book '{book_id}' not in corpus. Fetch it first."

    meta      = index[book_id]
    text_path = CORPUS_DIR / f"{book_id}.txt"
    if not text_path.exists():
        meta["status"] = "pending"  # lost the file, needs re-fetch
        _save_index(index)
        return f"Text file missing for '{meta['title']}' (id={book_id}). Re-fetch needed."

    text = text_path.read_text(encoding="utf-8", errors="replace")

    # Split into paragraphs
    raw_paras = [p.strip() for p in text.split("\n\n")]
    paras     = [p for p in raw_paras if len(p) >= MIN_PARA_CHARS]

    cursor  = meta.get("para_cursor", 0)
    remaining = paras[cursor:]

    if not remaining:
        meta["status"] = "complete"
        meta["train_ts"] = _now()
        _save_index(index)
        return f"'{meta['title']}' was already fully trained (cursor={cursor}/{len(paras)})."

    # Cap to MAX_PARAS_PER_BOOK total across all training runs for this book
    already_trained = cursor
    budget = max(0, MAX_PARAS_PER_BOOK - already_trained)
    batch  = remaining[:budget]

    meta["status"] = "in_progress"
    _save_index(index)

    trained = 0
    for i, para in enumerate(batch):
        doc_id = f"corpus_{book_id}_{cursor + i:05d}"
        word_graph.index(doc_id, para, weight=1.0)
        trained += 1

    word_graph.build_idf()
    word_graph.save(wg_save_path)

    new_cursor = cursor + trained
    if new_cursor >= len(paras) or trained >= budget:
        meta["status"]    = "complete"
        meta["train_ts"]  = _now()
    meta["para_cursor"] = new_cursor
    _save_index(index)

    # Evict if disk is getting tight
    evict_note = ""
    if _disk_free_gb() < EVICT_THRESHOLD_GB:
        evict_note = " | " + evict()

    return (
        f"Trained '{meta['title']}': {trained} paragraphs indexed "
        f"(cursor {cursor}→{new_cursor}/{len(paras)}), "
        f"status={meta['status']}. "
        f"Word graph saved.{evict_note}"
    )


# ── Eviction ───────────────────────────────────────────────────────────────────

def evict() -> str:
    """
    Evict corpus files to free space.
    Priority: complete → in_progress → pending.
    Stops when disk free >= EVICT_THRESHOLD_GB or corpus is empty.
    Returns a summary of what was deleted.
    """
    index   = _load_index()
    deleted = []

    def _try_evict_group(status: str) -> None:
        for book_id, meta in list(index.items()):
            if _disk_free_gb() >= EVICT_THRESHOLD_GB:
                return
            if meta["status"] == status:
                text_path = CORPUS_DIR / f"{book_id}.txt"
                if text_path.exists():
                    text_path.unlink(missing_ok=True)
                deleted.append(f"'{meta['title']}' ({status})")
                del index[book_id]

    _try_evict_group("complete")
    _try_evict_group("in_progress")
    _try_evict_group("pending")

    if deleted:
        _save_index(index)
        return f"Evicted {len(deleted)} book(s): {'; '.join(deleted)}. Disk free: {_disk_free_gb():.2f} GB."
    return f"Nothing to evict. Disk free: {_disk_free_gb():.2f} GB."


# ── Status ─────────────────────────────────────────────────────────────────────

def load_url_list(path: str) -> list[str]:
    """
    Read a file containing one URL per line (e.g. Gutenberg reading list).
    Returns a list of URLs, stripping comments (#) and blank lines.
    """
    fpath = Path(path).expanduser()
    if not fpath.exists():
        return []
    urls = []
    for line in fpath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line.startswith("http"):
            urls.append(line)
    return urls


def list_books() -> str:
    """Return a formatted summary of the training corpus."""
    index = _load_index()
    if not index:
        return (
            "Training corpus is empty.\n"
            f"  Local source dir: {local_source_dir()} "
            f"({'exists' if local_source_dir().exists() else 'not found yet'})\n"
            "  Use fetch_training_text() or scan_local_training_source() to add books."
        )

    lines = [f"Training corpus — {len(index)} book(s):"]
    by_status: dict[str, list] = {"pending": [], "in_progress": [], "complete": []}
    for book_id, meta in index.items():
        by_status.setdefault(meta["status"], []).append((book_id, meta))

    for status in ("in_progress", "pending", "complete"):
        group = by_status.get(status, [])
        if not group:
            continue
        lines.append(f"\n  [{status.upper()}]")
        for book_id, meta in group:
            cursor = meta.get("para_cursor", 0)
            kb     = meta.get("size_bytes", 0) // 1024
            lines.append(
                f"    {book_id}  {meta['title'][:50]:<50}  "
                f"{kb:>6} KB  cursor={cursor}  src={meta['source']}"
            )

    lines.append(f"\n  Disk free: {_disk_free_gb():.2f} GB")
    lines.append(f"  Local source: {local_source_dir()} "
                 f"({'exists' if local_source_dir().exists() else 'not found yet'})")
    return "\n".join(lines)

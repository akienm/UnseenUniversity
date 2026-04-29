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
import logging

import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .word_graph import WordGraph

from ..igor_base import get_logger
from ..paths import paths

CORPUS_DIR = paths().training_corpus
INDEX_FILE = CORPUS_DIR / "index.json"

# Paragraphs < this many chars are skipped (headers, short lines)
MIN_PARA_CHARS = 60
# Max paragraphs indexed per book (caps per-book training scope)
MAX_PARAS_PER_BOOK = int(os.getenv("IGOR_TRAINING_MAX_PARAS", "800"))
# Disk free threshold below which eviction triggers (GB)
EVICT_THRESHOLD_GB = float(os.getenv("IGOR_DISK_WARN_GB", "1.0"))

# Spacing effect: inter-trial intervals for re-training passes (days).
# Each completed pass advances to the next interval; last interval repeats.
# Override with IGOR_TRAINING_SPACING_DAYS as comma-separated ints.
_default_spacing = "1,3,7,21"
SPACING_INTERVALS_DAYS: list[int] = [
    int(x) for x in os.getenv("IGOR_TRAINING_SPACING_DAYS", _default_spacing).split(",")
]


def _book_id(url_or_path: str) -> str:
    """Stable 10-char ID from URL or path."""
    return hashlib.sha256(url_or_path.encode()).hexdigest()[:10]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _disk_free_gb() -> float:
    usage = shutil.disk_usage(str(CORPUS_DIR.parent))
    return usage.free / (1024**3)


def _load_index() -> dict:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception as _bare_e:
            get_logger(__name__).warning(
                "bare except in wild_igor/igor/cognition/training_corpus.py: %s",
                _bare_e,
            )
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
    index = _load_index()

    if book_id in index:
        existing = index[book_id]
        return book_id, (
            f"Already in corpus: '{existing['title']}' "
            f"(status={existing['status']}, id={book_id})"
        )

    # Check CPU/RAM load before fetching — bulk training was OOM-crashing the process.
    # Soft gate: warn but proceed on "warn"; hard gate: abort on "critical".
    try:
        from ..network.system_proxy import system_proxy as _sp

        _snap = _sp.snapshot()
        _mem = _snap.memory
        _load1 = os.getloadavg()[0]
        _ncpus = os.cpu_count() or 1
        _load_pct = _load1 / _ncpus * 100
        _ram_crit = float(os.getenv("IGOR_LOAD_RAM_CRIT", "92"))
        _swap_crit = float(os.getenv("IGOR_LOAD_SWAP_CRIT", "75"))
        _cpu_crit = float(os.getenv("IGOR_LOAD_CPU_CRIT", "95"))
        _ram_pct = _mem.percent if _mem else 0.0
        _swap_pct = _mem.swap_percent if _mem else 0.0
        if _ram_pct >= _ram_crit or _swap_pct >= _swap_crit or _load_pct >= _cpu_crit:
            return "", (
                f"Aborted fetch — system under critical load "
                f"(RAM {_ram_pct:.0f}%, swap {_swap_pct:.0f}%, CPU {_load_pct:.0f}%). "
                f"Try again when the machine is less busy."
            )
    except Exception as _bare_e:
        get_logger(__name__).warning(
            "bare except in wild_igor/igor/cognition/training_corpus.py: %s", _bare_e
        )

    # Check disk before fetching
    free_gb = _disk_free_gb()
    if free_gb < EVICT_THRESHOLD_GB:
        evict_msg = evict()
        free_gb = _disk_free_gb()
        if free_gb < 0.2:
            return (
                "",
                f"Disk critically low ({free_gb:.2f} GB free) even after eviction. Aborting fetch.",
            )

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; IgorWordGraphTrainer/1.0)"}
    # Hard cap: don't load a document larger than this into memory at once.
    # 5MB+ papers were OOM-crashing the process. 1MB is plenty for training.
    MAX_FETCH_CHARS = int(os.getenv("IGOR_TRAINING_MAX_CHARS", str(1_000_000)))

    try:
        resp = requests.get(url, headers=headers, timeout=30, verify=certifi.where())
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        return "", f"Fetch failed: {e}"

    if len(text) > MAX_FETCH_CHARS:
        text = text[:MAX_FETCH_CHARS]

    text_path = CORPUS_DIR / f"{book_id}.txt"
    text_path.write_text(text, encoding="utf-8", errors="replace")

    index[book_id] = {
        "title": title,
        "url": url,
        "source": source,
        "status": "pending",
        "fetch_ts": _now(),
        "train_ts": None,
        "size_bytes": len(text.encode("utf-8")),
        "para_cursor": 0,
    }
    _save_index(index)
    return book_id, (
        f"Fetched '{title}' ({len(text):,} chars) → id={book_id}, status=pending. "
        f"Disk free: {_disk_free_gb():.2f} GB."
    )


# ── Local source ───────────────────────────────────────────────────────────────


def local_source_dir() -> Path:
    return Path(
        os.getenv("IGOR_TRAINING_SOURCE_DIR", str(Path.home() / "TheIgorsProject"))
    )


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

    index = _load_index()
    added = []
    already = []
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    for fpath in files:
        book_id = _book_id(str(fpath))
        if book_id in index:
            already.append(fpath.name)
            continue
        text = fpath.read_text(encoding="utf-8", errors="replace")
        text_path = CORPUS_DIR / f"{book_id}.txt"
        # Symlink if in same filesystem, else copy
        try:
            text_path.symlink_to(fpath.resolve())
        except Exception:
            text_path.write_text(text, encoding="utf-8")
        index[book_id] = {
            "title": fpath.stem,
            "url": str(fpath.resolve()),
            "source": "local",
            "status": "pending",
            "fetch_ts": _now(),
            "train_ts": None,
            "size_bytes": fpath.stat().st_size,
            "para_cursor": 0,
        }
        added.append(fpath.name)

    _save_index(index)
    lines = [f"Scanned {src}:"]
    if added:
        lines.append(
            f"  Registered {len(added)} new file(s): {', '.join(added[:5])}"
            + (" ..." if len(added) > 5 else "")
        )
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

    meta = index[book_id]
    text_path = CORPUS_DIR / f"{book_id}.txt"
    if not text_path.exists():
        meta["status"] = "pending"  # lost the file, needs re-fetch
        _save_index(index)
        return (
            f"Text file missing for '{meta['title']}' (id={book_id}). Re-fetch needed."
        )

    text = text_path.read_text(encoding="utf-8", errors="replace")

    # Split into paragraphs
    raw_paras = [p.strip() for p in text.split("\n\n")]
    paras = [p for p in raw_paras if len(p) >= MIN_PARA_CHARS]

    cursor = meta.get("para_cursor", 0)
    remaining = paras[cursor:]

    if not remaining:
        meta["status"] = "complete"
        meta["train_ts"] = _now()
        _save_index(index)
        return f"'{meta['title']}' was already fully trained (cursor={cursor}/{len(paras)})."

    # Cap to MAX_PARAS_PER_BOOK total across all training runs for this book
    already_trained = cursor
    budget = max(0, MAX_PARAS_PER_BOOK - already_trained)
    batch = remaining[:budget]

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
        meta["status"] = "complete"
        meta["train_ts"] = _now()
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


# ── Spacing / inter-trial intervals ────────────────────────────────────────────


def _next_pass_ts(pass_count: int, anchor_ts: str) -> str:
    """
    Compute the ISO timestamp for the next training pass.
    pass_count: how many passes have already been completed.
    anchor_ts: ISO timestamp of the last training event.
    The gap is SPACING_INTERVALS_DAYS[pass_count], clamped to last element.
    """
    idx = min(pass_count, len(SPACING_INTERVALS_DAYS) - 1)
    gap_days = SPACING_INTERVALS_DAYS[idx]
    anchor_dt = datetime.fromisoformat(anchor_ts).replace(tzinfo=None)
    next_dt = anchor_dt + timedelta(days=gap_days)
    return next_dt.strftime("%Y-%m-%dT%H:%M:%S")


def schedule_training_passes(reset: bool = False) -> str:
    """
    Schedule inter-trial re-training passes for all complete books.
    Sets next_pass_ts based on train_ts + first spacing interval.
    If reset=True, clears pass_count and resets next_pass_ts from train_ts.
    Returns a summary.
    """
    index = _load_index()
    scheduled = []
    already = []

    for book_id, meta in index.items():
        if meta["status"] != "complete":
            continue
        train_ts = meta.get("train_ts")
        if not train_ts:
            continue  # never finished a pass — skip

        existing_next = meta.get("next_pass_ts")
        pass_count = meta.get("pass_count", 0)

        if reset:
            meta["pass_count"] = 0
            meta["next_pass_ts"] = _next_pass_ts(0, train_ts)
            scheduled.append(meta["title"][:40])
        elif existing_next:
            already.append(meta["title"][:40])
        else:
            meta["next_pass_ts"] = _next_pass_ts(pass_count, train_ts)
            meta["pass_count"] = pass_count
            scheduled.append(meta["title"][:40])

    if scheduled or reset:
        _save_index(index)

    lines = [
        f"schedule_training_passes: {len(scheduled)} scheduled, {len(already)} already had a schedule."
    ]
    if scheduled:
        lines.append(
            f"  Newly scheduled ({len(scheduled)}): "
            + ", ".join(scheduled[:5])
            + (" ..." if len(scheduled) > 5 else "")
        )
    lines.append(f"  Spacing intervals: {SPACING_INTERVALS_DAYS} days")
    return "\n".join(lines)


def train_due_passes(dry_run: bool = False) -> str:
    """
    Run a re-training pass for all books where next_pass_ts <= now.
    Resets para_cursor to 0 (full re-scan), increments pass_count,
    sets next_pass_ts to the next spacing interval.
    If dry_run=True, just report what would run without training.
    Returns a summary.
    """
    from ..cognition.word_graph import WordGraph, default_cache_path

    index = _load_index()
    now_str = _now()
    due = [
        (book_id, meta)
        for book_id, meta in index.items()
        if meta.get("next_pass_ts")
        and meta["next_pass_ts"] <= now_str
        and meta["status"] == "complete"
    ]

    if not due:
        return f"No training passes due (now={now_str})."

    if dry_run:
        lines = [f"Due for re-training ({len(due)} books):"]
        for book_id, meta in due:
            lines.append(
                f"  {meta['title'][:50]}: pass #{meta.get('pass_count', 0)+1}, "
                f"next_pass_ts={meta['next_pass_ts']}"
            )
        return "\n".join(lines)

    wg = WordGraph.load(default_cache_path())
    save_path = default_cache_path()
    results = []

    for book_id, meta in due:
        if _disk_free_gb() < 0.2:
            results.append("Disk critically low — stopping.")
            break

        text_path = CORPUS_DIR / f"{book_id}.txt"
        if not text_path.exists():
            results.append(
                f"  Skipped '{meta['title'][:40]}': text file missing (needs re-fetch)."
            )
            continue

        pass_num = meta.get("pass_count", 0) + 1
        # Reset cursor for a full re-scan
        meta["para_cursor"] = 0
        _save_index(index)

        msg = train(book_id, wg, save_path)

        # Advance spacing schedule
        meta["pass_count"] = pass_num
        meta["next_pass_ts"] = _next_pass_ts(pass_num, meta["train_ts"])
        _save_index(index)

        results.append(f"  Pass #{pass_num} — {msg}")

    lines = [f"train_due_passes: {len(due)} due, {len(results)} processed:"]
    lines.extend(results)
    return "\n".join(lines)


# ── Eviction ───────────────────────────────────────────────────────────────────


def evict() -> str:
    """
    Evict corpus files to free space.
    Priority: complete → in_progress → pending.
    Stops when disk free >= EVICT_THRESHOLD_GB or corpus is empty.
    Returns a summary of what was deleted.
    """
    index = _load_index()
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
            kb = meta.get("size_bytes", 0) // 1024
            lines.append(
                f"    {book_id}  {meta['title'][:50]:<50}  "
                f"{kb:>6} KB  cursor={cursor}  src={meta['source']}"
            )

    lines.append(f"\n  Disk free: {_disk_free_gb():.2f} GB")
    lines.append(
        f"  Local source: {local_source_dir()} "
        f"({'exists' if local_source_dir().exists() else 'not found yet'})"
    )
    return "\n".join(lines)

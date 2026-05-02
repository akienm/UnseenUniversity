"""reading_engine.py — Fetch/process engine for the unified reading tool.

Two phases:
  1. fetch_to_blob(source) → copies source material to a local JSON blob
  2. process_blob(blob_path, ...) → reads blob, chunks, extracts nodes, deposits

Reuses:
  - ebook_reader.open_book / read_chunk for format parsing
  - book_learner extraction prompts and deposit logic (imported directly)

All DB interaction uses db_proxy (HOME DB, shared across instances).
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

from ..memory.cortex import Cortex
from ..memory.db_proxy import DatabaseProxy, make_home_proxy
from ..paths import paths

# ── Lazy imports (heavy modules, defer until needed) ─────────────────────────

_cortex: Cortex | None = None
_db: DatabaseProxy | None = None


def _get_db() -> DatabaseProxy:
    global _db
    if _db is None:
        _db = make_home_proxy()
    return _db


def _get_cortex() -> Cortex:
    global _cortex
    if _cortex is None:
        _cortex = Cortex()
    return _cortex


def _instance_id() -> str:
    return os.getenv("IGOR_INSTANCE_ID", socket.gethostname())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Blob key ─────────────────────────────────────────────────────────────────


def blob_key(source: str) -> str:
    """Deterministic blob key from source URL."""
    return hashlib.md5(source.encode()).hexdigest()[:16]


def blob_path(source: str) -> Path:
    """Full path to blob file for a source."""
    d = paths().reading_blobs
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{blob_key(source)}.json"


# ── Phase 1: Fetch ───────────────────────────────────────────────────────────


def fetch_to_blob(source: str, title: str = "", author: str = "") -> dict:
    """Copy source material to a local JSON blob.

    Opens the book via ebook_reader (handles calibre://, file://, http(s)://),
    serializes the parsed sentences + chapter structure to a local JSON file.
    Returns dict with blob_path and metadata, or raises on failure.
    """
    from .ebook_reader import open_book, open_book_url, read_chunk

    # Open the book (one network read)
    if source.startswith("calibre://"):
        cid = int(source.replace("calibre://", ""))
        handle = open_book(calibre_id=cid, resume=False)
    elif source.startswith("http://") or source.startswith("https://"):
        handle = open_book_url(source, title=title or source)
    elif source.startswith("file://"):
        fpath = source.replace("file://", "")
        handle = open_book(path=fpath, resume=False)
    else:
        # Try as title search
        handle = open_book(title=source, resume=False)

    if isinstance(handle, str):
        raise RuntimeError(f"Failed to open: {handle}")

    handle_key = handle["_handle_key"]
    book_title = handle.get("title", title or source)
    book_author = handle.get("author", author or "")
    total = handle.get("total_sentences", 0)

    # Read ALL sentences in one pass by reading chunks until at_end
    all_sentences = []
    chapter_breaks = []
    chapter_titles_list = []
    current_chapter = None

    # Reset position to 0
    from .ebook_reader import _HANDLE_CACHE

    bh = _HANDLE_CACHE.get(handle_key)
    if bh:
        bh.position = 0
        # Extract chapter structure directly from BookHandle
        chapter_breaks = list(bh.chapter_breaks) if bh.chapter_breaks else []
        chapter_titles_list = list(bh.chapter_titles) if bh.chapter_titles else []
        all_sentences = list(bh.sentences)
    else:
        # Fallback: read via chunks
        while True:
            chunk = read_chunk(handle_key=handle_key, n=100)
            if isinstance(chunk, str):
                break
            all_sentences.extend(chunk.get("sentences", []))
            if chunk.get("at_end"):
                break

    # Serialize to blob
    bp = blob_path(source)
    blob_data = {
        "source": source,
        "title": book_title,
        "author": book_author,
        "fetched_at": _now_iso(),
        "fetched_by": _instance_id(),
        "format": handle.get("fmt", "unknown"),
        "total_sentences": len(all_sentences),
        "sentences": all_sentences,
        "chapter_breaks": chapter_breaks,
        "chapter_titles": chapter_titles_list,
    }
    bp.write_text(json.dumps(blob_data, ensure_ascii=False))

    return {
        "blob_path": str(bp),
        "title": book_title,
        "author": book_author,
        "total_sentences": len(all_sentences),
        "chapters": len(chapter_titles_list),
    }


# ── Phase 2: Process ─────────────────────────────────────────────────────────


def count_chunks(blob_file: str | Path, chunk_size: int = 15) -> int:
    """Return the number of chunks a blob will decompose into.

    Used by the reading-campaign worker to lazy-expand item → chunks at
    claim time without having to run the full extraction pipeline.
    """
    bp = Path(blob_file)
    data = json.loads(bp.read_text())
    total = len(data.get("sentences", []))
    if total <= 0:
        return 0
    return (total + chunk_size - 1) // chunk_size


def process_one_chunk(
    blob_file: str | Path,
    chunk_pos: int,
    pass_number: int = 1,
    use_local: bool = True,
    chunk_size: int = 15,
    model: str = "",
) -> dict:
    """Process a SINGLE chunk at a specific position within a blob.

    Reading-worker-pool entry point: a worker that has claimed a block
    (campaign_id, reading_list_id, chunk_pos) calls this to do exactly
    one chunk of extraction, deposit the resulting nodes, and return a
    small result dict to ack the block.

    Returns dict with keys: node_count, model_used, inference_tier,
    summary, chunk_pos, at_end (True if chunk_pos is past blob end).

    Contrasts with process_blob which walks chunks sequentially; this is
    the per-block grain the worker pool needs.
    """
    import sys

    repo = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "lab"))

    from claudecode.book_learner import (
        _deposit_nodes,
        _ensure_book_node,
        _ensure_chapter_node,
        _extract_nodes,
    )

    bp = Path(blob_file)
    blob = json.loads(bp.read_text())
    sentences = blob.get("sentences", [])
    chapter_breaks = blob.get("chapter_breaks", [])
    chapter_titles = blob.get("chapter_titles", [])
    book_title = blob.get("title", "")
    book_author = blob.get("author", "")
    source = blob.get("source", "")

    if chunk_pos >= len(sentences):
        return {
            "node_count": 0,
            "model_used": "",
            "inference_tier": "",
            "summary": "chunk position past blob end",
            "chunk_pos": chunk_pos,
            "at_end": True,
        }

    chunk_sentences = sentences[chunk_pos : chunk_pos + chunk_size]
    if not chunk_sentences:
        return {
            "node_count": 0,
            "model_used": "",
            "inference_tier": "",
            "summary": "empty chunk",
            "chunk_pos": chunk_pos,
            "at_end": True,
        }

    # Determine current chapter from break positions
    chapter_num = 0
    chapter_title = ""
    for i, cb in enumerate(chapter_breaks):
        if chunk_pos >= cb:
            chapter_num = i + 1
            chapter_title = chapter_titles[i] if i < len(chapter_titles) else ""

    cortex = _get_cortex()
    book_node_id = _ensure_book_node(cortex, book_title, book_author)
    chapter_node_id = _ensure_chapter_node(
        cortex, book_node_id, book_title, chapter_num, chapter_title
    )

    # Pass-2 system prompt support
    system_prompt = None
    if pass_number == 2:
        from claudecode.book_learner import _EXTRACT_PROMPT_PASS2, _build_watch_context

        watch_ctx = _build_watch_context()
        system_prompt = _EXTRACT_PROMPT_PASS2.format(watch_context=watch_ctx)

    if not model:
        model = os.getenv("BOOK_LEARNER_MODEL", "openai/gpt-4o-mini")

    chunk_text = " ".join(chunk_sentences)
    result = _extract_nodes(
        chunk_text,
        model=model,
        chapter_title=chapter_title,
        local=use_local,
        system_prompt=system_prompt,
    )

    nodes = result.get("nodes", [])
    model_used = result.get("model_used") or ("local-ollama" if use_local else model)
    inference_tier = result.get("inference_tier") or ("local" if use_local else "cloud")

    deposited = 0
    if nodes:
        deposited = _deposit_nodes(
            nodes,
            cortex,
            book_title,
            chunk_pos=chunk_pos,
            chapter_node_id=chapter_node_id,
            pass2=(pass_number == 2),
            model_used=model_used,
            author=book_author,
            campaign_id=source,
        )

    return {
        "node_count": deposited,
        "model_used": model_used,
        "inference_tier": inference_tier,
        "summary": (result.get("summary") or "")[:120],
        "chunk_pos": chunk_pos,
        "at_end": False,
    }


def process_blob(
    blob_file: str | Path,
    pass_number: int = 1,
    use_local: bool = True,
    chunk_size: int = 15,
    delay: float = 1.5,
    limit: int | None = None,
    model: str = "",
) -> dict:
    """Process a blob: chunk sentences, extract nodes via LLM, deposit to memory.

    Returns dict with node_count, edge_count, chunks_processed, processing_seconds.
    """
    import sys
    import uuid

    # Add repo to path for book_learner imports
    repo = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "wild_igor"))

    # Import extraction functions from book_learner
    from claudecode.book_learner import (
        _arousal_from_cp,
        _deposit_nodes,
        _ensure_book_node,
        _ensure_chapter_node,
        _extract_nodes,
    )

    # Load blob
    blob_file = Path(blob_file)
    blob_data = json.loads(blob_file.read_text())
    sentences = blob_data["sentences"]
    chapter_breaks = blob_data.get("chapter_breaks", [])
    chapter_titles = blob_data.get("chapter_titles", [])
    book_title = blob_data["title"]
    book_author = blob_data.get("author", "")

    if not model:
        model = os.getenv("BOOK_LEARNER_MODEL", "openai/gpt-4o-mini")

    cortex = _get_cortex()

    # Ensure spine nodes
    book_node_id = _ensure_book_node(cortex, book_title, book_author)

    # Build pass-2 prompt if needed
    system_prompt = None
    if pass_number == 2:
        from claudecode.book_learner import _EXTRACT_PROMPT_PASS2, _build_watch_context

        watch_ctx = _build_watch_context()
        system_prompt = _EXTRACT_PROMPT_PASS2.format(watch_context=watch_ctx)

    # Process in chunks
    total_nodes = 0
    total_edges = 0
    chunks_processed = 0
    start_time = time.monotonic()

    pos = 0
    while pos < len(sentences):
        chunk_sentences = sentences[pos : pos + chunk_size]
        if not chunk_sentences:
            break

        # Determine current chapter
        chapter_num = 0
        chapter_title = ""
        for i, cb in enumerate(chapter_breaks):
            if pos >= cb:
                chapter_num = i + 1
                chapter_title = chapter_titles[i] if i < len(chapter_titles) else ""

        # Ensure chapter node
        chapter_node_id = _ensure_chapter_node(
            cortex, book_node_id, book_title, chapter_num, chapter_title
        )

        chunk_text = " ".join(chunk_sentences)

        # Extract
        result = _extract_nodes(
            chunk_text,
            model=model,
            chapter_title=chapter_title,
            local=use_local,
            system_prompt=system_prompt,
        )

        nodes = result.get("nodes", [])
        # T-reading-audit-qwen-complete: take model_used from the extractor's
        # result, not from env. The cluster_router decided which model to use
        # inside _extract_nodes_local; that decision now rides back with the
        # nodes so every deposit records the exact (model, tier) pair.
        _model_used = result.get("model_used") or (
            "local-ollama" if use_local else model
        )
        # T-extract-prompt-zero-tolerance diagnostic: log raw extractor output
        # so post-mortem analysis can see why a chunk produced 0 kept nodes.
        # Records: extracted count, confidence distribution, any parse-fail
        # summary, and the chunk's first 40 chars for pinpointing.
        try:
            import logging as _ldiag

            _confs = [
                float(n.get("confidence", 0))
                for n in nodes
                if isinstance(n, dict) and n.get("confidence") is not None
            ]
            _ldiag.getLogger("igor.tools.reading_engine").info(
                "[extract-diag] chunk_pos=%d raw_nodes=%d confs=%s summary=%r chunk_head=%r",
                pos,
                len(nodes),
                [round(c, 2) for c in _confs],
                (result.get("summary") or "")[:80],
                chunk_text[:40],
            )
        except Exception:
            pass
        if nodes:
            deposited = _deposit_nodes(
                nodes,
                cortex,
                book_title,
                chunk_pos=pos,
                chapter_node_id=chapter_node_id,
                pass2=(pass_number == 2),
                model_used=_model_used,
                author=book_author,
                campaign_id=blob_data.get("source", ""),
            )
            total_nodes += deposited
            # Rough edge estimate: CP wiring + chapter wiring per node
            total_edges += deposited * 2

        chunks_processed += 1
        pos += chunk_size

        if limit and chunks_processed >= limit:
            break

        if delay > 0 and pos < len(sentences):
            time.sleep(delay)

    elapsed = int(time.monotonic() - start_time)

    # Deposit completion record
    from claudecode.book_learner import _deposit_completion_record

    status = "complete" if pos >= len(sentences) else "partial"
    # Prefer the actual model the extractor used (threaded from the last chunk's
    # result); fall back to the generic label only if we never got one back.
    _model = (result.get("model_used") if "result" in dir() else None) or (
        "local-ollama" if use_local else model
    )
    _deposit_completion_record(
        cortex,
        book_title,
        book_author,
        f"{book_title}|{blob_data.get('source', '')}",
        None,  # calibre_id
        len(sentences),
        chunks_processed,
        total_nodes,
        status,
        model_used=_model,
    )

    return {
        "node_count": total_nodes,
        "edge_count": total_edges,
        "embedding_count": 0,  # counted during deposit
        "chunks_processed": chunks_processed,
        "processing_seconds": elapsed,
        "model_used": _model,
        "status": status,
    }


# ── Run log writing ──────────────────────────────────────────────────────────


def write_run_log_header(run_id: str, label: str, items: list[dict]) -> Path:
    """Write the starting slate to a run log file. Returns log path."""
    log_dir = paths().reading_run_logs
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.log"

    lines = [
        f"== {run_id}: {label} ==",
        f"Created: {_now_iso()}",
        "",
        f"== STARTING SLATE ({len(items)} items) ==",
    ]
    for i, item in enumerate(items, 1):
        lines.append(
            f"  {i}. [{item.get('status', 'pending')}] {item['source']}  "
            f"{item.get('title', '')} — {item.get('author', '')}"
        )
    lines.append("")
    lines.append("== PROCESSING LOG ==")
    lines.append("")

    log_path.write_text("\n".join(lines))
    return log_path


def append_run_log(run_id: str, message: str) -> None:
    """Append a line to a run's log file."""
    log_path = paths().reading_run_logs / f"{run_id}.log"
    if log_path.exists():
        with open(log_path, "a") as f:
            f.write(f"{_now_iso()}  {message}\n")


def close_run_log(
    run_id: str,
    reason: str,
    completed: int,
    failed: int,
    skipped: int,
    total_nodes: int,
    total_edges: int,
) -> None:
    """Write the closing summary to a run log."""
    log_path = paths().reading_run_logs / f"{run_id}.log"
    if log_path.exists():
        with open(log_path, "a") as f:
            f.write(f"\n== CLOSED {_now_iso()} reason={reason} ==\n")
            f.write(f"Completed: {completed}  Failed: {failed}  Skipped: {skipped}\n")
            f.write(f"Total nodes: {total_nodes}  Total edges: {total_edges}\n")

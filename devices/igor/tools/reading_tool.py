"""reading_tool.py — Unified reading tool for Igor.

One tool, two modes (foreground / background), full self-visibility.
Registered as 'reading' with command dispatch.

Run state lives in Igor's memory graph as EPISODIC nodes:
  READING_RUN_xxx         — run node (parent)
  READING_RUN_xxx_001     — item nodes (children)

Coordination (multi-instance claiming) uses reading_list table.
Self-knowledge (what happened, how well) lives in the graph.

Commands:
  create_run    — batch pending books into a run
  list_runs     — show all runs with status
  run_status    — detailed per-item breakdown
  close_run     — seal a run; unclaimed items revert
  start_run     — begin processing (fg or bg)
  process_next  — claim + process next item (called by workers)
  read_now      — interactive: one book, synchronous
  master_index  — all books ever read
  run_log       — combined slate + processing log
  my_reading    — self-visibility: what am I doing now?
"""

from __future__ import annotations

import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..memory.cortex import Cortex
from ..memory.db_proxy import make_home_proxy
from ..memory.models import Memory, MemoryType
from ..paths import paths
from .registry import Tool, registry


def _db():
    return make_home_proxy()


def _cortex() -> Cortex:
    from ..paths import paths as _paths

    db_path = Path(
        os.environ.get(
            "IGOR_DB_PATH",
            _paths().instance / "wild-0001.db",
        )
    )
    return Cortex(db_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _instance_id() -> str:
    return os.getenv("IGOR_INSTANCE_ID", socket.gethostname())


def _next_run_id() -> str:
    """Generate next run ID for today: RUN_YYYYMMDD_NNN."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"RUN_{today}_"
    # Search memory graph for existing runs today
    cx = _cortex()
    # Use DB query for efficiency — memory nodes have deterministic IDs
    with _db()() as conn:
        rows = conn.execute(
            "SELECT id FROM memories WHERE id LIKE %s ORDER BY id DESC LIMIT 1",
            (f"READING_{prefix}%",),
        ).fetchall()
    if rows:
        # Parse the number from READING_RUN_YYYYMMDD_NNN
        last_id = rows[0]["id"]
        parts = last_id.replace("READING_", "").split("_")
        if len(parts) >= 3:
            try:
                last_num = int(parts[-1])
                return f"{prefix}{last_num + 1:03d}"
            except ValueError as _exc:
                from ..cognition.forensic_logger import log_error as _le
                _le(kind="SILENT_EXCEPT", detail=f"reading_tool.py:85: {_exc}")
    return f"{prefix}001"


# ── Commands ─────────────────────────────────────────────────────────────────


def _create_run(
    label: str = "",
    limit: int = 20,
    pass_number: int = 1,
    source_filter: str = "pending",
    items: str = "",
    **_kw,
) -> str:
    """Create a run. Claims items from reading_list, creates Memory nodes."""
    run_id = _next_run_id()
    run_node_id = f"READING_{run_id}"
    cx = _cortex()

    # Atomically claim items from reading_list (coordination table)
    with _db()() as conn:
        if items:
            sources = [s.strip() for s in items.split(",") if s.strip()]
            placeholders = ",".join(["%s"] * len(sources))
            rows = conn.execute(
                f"UPDATE reading_list SET status='queued', run_id=%s "
                f"WHERE source IN ({placeholders}) AND status=%s "
                f"RETURNING source, title, author",
                (run_id, *sources, source_filter),
            ).fetchall()
        else:
            rows = conn.execute(
                "UPDATE reading_list SET status='queued', run_id=%s "
                "WHERE source IN ("
                "  SELECT source FROM reading_list"
                "  WHERE status=%s AND run_id IS NULL"
                "  AND source IS NOT NULL AND source != ''"
                "  ORDER BY encoding_arousal DESC, priority ASC"
                "  LIMIT %s"
                ") RETURNING source, title, author",
                (run_id, source_filter, limit),
            ).fetchall()

    if not rows:
        return f"[reading] No items to claim (filter={source_filter})"

    # Create run Memory node (EPISODIC — this is Igor's self-knowledge)
    run_label = label or run_id
    item_list = []
    for i, row in enumerate(rows):
        item_list.append(
            {
                "source": row["source"],
                "title": row["title"] or "",
                "author": row["author"] or "",
            }
        )

    run_mem = Memory(
        id=run_node_id,
        narrative=(
            f"Reading run {run_id}: {run_label}. "
            f"{len(rows)} items queued for pass-{pass_number} extraction. "
            f"Books: {', '.join(r['title'] or r['source'] for r in rows)}"
        ),
        memory_type=MemoryType.EPISODIC,
        source="reading_tool",
        confidence=1.0,
        context_of_encoding="reading_run|created",
        metadata={
            "run_id": run_id,
            "label": run_label,
            "status": "draft",
            "created_at": _now_iso(),
            "created_by": _instance_id(),
            "pass_number": pass_number,
            "item_count": len(rows),
            "completed_count": 0,
            "failed_count": 0,
        },
    )
    cx.store(run_mem)

    # Create item Memory nodes as children of the run
    for i, item in enumerate(item_list, 1):
        item_node_id = f"{run_node_id}_{i:03d}"
        item_mem = Memory(
            id=item_node_id,
            narrative=(
                f"Reading item: {item['title'] or item['source']}"
                f"{' by ' + item['author'] if item['author'] else ''}"
                f" — queued in run {run_id}"
            ),
            memory_type=MemoryType.EPISODIC,
            parent_id=run_node_id,
            source="reading_tool",
            confidence=1.0,
            context_of_encoding="reading_run|item",
            metadata={
                "run_id": run_id,
                "item_index": i,
                "source": item["source"],
                "title": item["title"],
                "author": item["author"],
                "status": "pending",
            },
        )
        cx.store(item_mem)
        try:
            cx.add_child(run_node_id, item_node_id)
        except Exception as _exc:
            from ..cognition.forensic_logger import log_error as _le
            _le(kind="SILENT_EXCEPT", detail=f"reading_tool.py:197: {_exc}")

    # Write run log header
    from .reading_engine import write_run_log_header

    write_run_log_header(run_id, run_label, item_list)

    return (
        f"[reading] Run {run_id} created: {len(rows)} items "
        f"(pass {pass_number}, label={run_label})"
    )


def _list_runs(status: str = "", limit: int = 20, **_kw) -> str:
    """List all runs by searching the memory graph."""
    with _db()() as conn:
        if status:
            rows = conn.execute(
                "SELECT id, narrative, metadata FROM memories "
                "WHERE id LIKE %s "
                "AND metadata->>'status' = %s "
                "AND context_of_encoding = 'reading_run|created' "
                "ORDER BY metadata->>'created_at' DESC LIMIT %s",
                ("READING_RUN_%", status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, narrative, metadata FROM memories "
                "WHERE id LIKE %s "
                "AND context_of_encoding = 'reading_run|created' "
                "ORDER BY metadata->>'created_at' DESC LIMIT %s",
                ("READING_RUN_%", limit),
            ).fetchall()

    if not rows:
        return "[reading] No runs found"

    lines = ["READING RUNS:"]
    for row in rows:
        meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
        st = meta.get("status", "?")
        label = meta.get("label", "")
        pn = meta.get("pass_number", 1)
        items = meta.get("item_count", 0)
        done = meta.get("completed_count", 0)
        failed = meta.get("failed_count", 0)
        lines.append(
            f"  [{st}] {row['id'].replace('READING_', '')} — {label} "
            f"(pass {pn}) {done}/{items} done"
            f"{f', {failed} failed' if failed else ''}"
        )
    return "\n".join(lines)


def _run_status(run_id: str = "", **_kw) -> str:
    """Detailed status of one run from the memory graph."""
    if not run_id:
        return "[reading] run_id required"

    run_node_id = f"READING_{run_id}" if not run_id.startswith("READING_") else run_id
    cx = _cortex()
    run_mem = cx.get(run_node_id)
    if not run_mem:
        return f"[reading] Run {run_id} not found in memory"

    meta = run_mem.metadata or {}
    lines = [
        f"== {meta.get('run_id', run_id)}: {meta.get('label', '')} (pass {meta.get('pass_number', 1)}) ==",
        f"Status: {meta.get('status', '?')}  Created: {meta.get('created_at', '?')}",
    ]
    if meta.get("started_at"):
        lines.append(f"Started: {meta['started_at']}")
    if meta.get("closed_at"):
        lines.append(f"Closed: {meta['closed_at']} ({meta.get('closed_reason', '')})")

    # Find child item nodes
    with _db()() as conn:
        items = conn.execute(
            "SELECT id, narrative, metadata FROM memories "
            "WHERE id LIKE %s AND context_of_encoding = 'reading_run|item'ORDER BY id",
            (f"{run_node_id}_%",),
        ).fetchall()

    total_nodes = 0
    total_edges = 0
    lines.append(f"\nItems ({len(items)}):")
    for item in items:
        imeta = item["metadata"] if isinstance(item["metadata"], dict) else {}
        st = imeta.get("status", "?")
        title = imeta.get("title", imeta.get("source", "?"))
        claimed = imeta.get("claimed_by", "")
        nodes = imeta.get("node_count", 0)
        edges = imeta.get("edge_count", 0)
        secs = imeta.get("processing_seconds", 0)

        line = f"  [{st:10s}] {title}"
        if claimed:
            line += f" ({claimed})"
        if nodes:
            line += f" nodes={nodes}"
            total_nodes += nodes
        if edges:
            total_edges += edges
        if secs:
            line += f" {secs}s"
        err = imeta.get("error_message", "")
        if err:
            line += f" ERR: {err[:50]}"
        lines.append(line)

    lines.append(f"\nTotals: {total_nodes} nodes, {total_edges} edges")
    return "\n".join(lines)


def _close_run(run_id: str = "", reason: str = "complete", **_kw) -> str:
    """Seal a run. Updates Memory nodes, reverts unclaimed reading_list items."""
    if not run_id:
        return "[reading] run_id required"

    run_node_id = f"READING_{run_id}" if not run_id.startswith("READING_") else run_id
    cx = _cortex()
    run_mem = cx.get(run_node_id)
    if not run_mem:
        return f"[reading] Run {run_id} not found"

    meta = run_mem.metadata or {}
    if meta.get("status") == "closed":
        return f"[reading] Run {run_id} already closed"

    # Count items by status from child nodes
    with _db()() as conn:
        items = conn.execute(
            "SELECT id, metadata FROM memories "
            "WHERE id LIKE %s AND context_of_encoding = 'reading_run|item'",
            (f"{run_node_id}_%",),
        ).fetchall()

    completed = 0
    failed = 0
    skipped = 0
    total_nodes = 0
    total_edges = 0

    for item in items:
        imeta = item["metadata"] if isinstance(item["metadata"], dict) else {}
        st = imeta.get("status", "pending")
        if st == "complete":
            completed += 1
            total_nodes += imeta.get("node_count", 0)
            total_edges += imeta.get("edge_count", 0)
        elif st == "failed":
            failed += 1
        elif st in ("pending", "fetched"):
            skipped += 1
            # Mark as skipped in memory
            imeta["status"] = "skipped"
            skip_mem = cx.get(item["id"])
            if skip_mem:
                skip_mem.metadata = imeta
                cx.store(skip_mem)

    # Revert unclaimed items in reading_list
    actual_run_id = meta.get("run_id", run_id)
    with _db()() as conn:
        conn.execute(
            "UPDATE reading_list SET status='pending', run_id=NULL, claimed_by=NULL "
            "WHERE run_id=%s AND status='queued'",
            (actual_run_id,),
        )

    # Update run Memory node
    meta["status"] = "closed"
    meta["closed_at"] = _now_iso()
    meta["closed_reason"] = reason
    meta["completed_count"] = completed
    meta["failed_count"] = failed
    run_mem.metadata = meta
    run_mem.narrative = (
        f"Reading run {actual_run_id}: {meta.get('label', '')} — CLOSED ({reason}). "
        f"{completed} completed, {failed} failed, {skipped} skipped. "
        f"{total_nodes} nodes, {total_edges} edges."
    )
    cx.store(run_mem)

    # Close log file
    from .reading_engine import close_run_log

    close_run_log(run_id, reason, completed, failed, skipped, total_nodes, total_edges)

    return (
        f"[reading] Run {actual_run_id} closed ({reason}). "
        f"complete={completed} failed={failed} skipped={skipped} "
        f"nodes={total_nodes} edges={total_edges}"
    )


def _start_run(run_id: str = "", mode: str = "background", **_kw) -> str:
    """Start processing a run."""
    if not run_id:
        return "[reading] run_id required"

    run_node_id = f"READING_{run_id}" if not run_id.startswith("READING_") else run_id
    cx = _cortex()
    run_mem = cx.get(run_node_id)
    if not run_mem:
        return f"[reading] Run {run_id} not found"

    meta = run_mem.metadata or {}
    if meta.get("status") == "closed":
        return f"[reading] Run {run_id} already closed"

    # Update run status
    meta["status"] = "active"
    meta["started_at"] = _now_iso()
    run_mem.metadata = meta
    cx.store(run_mem)

    actual_run_id = meta.get("run_id", run_id)

    if mode == "foreground":
        results = []
        while True:
            result = _process_next(run_id=actual_run_id)
            if "[no more items]" in result or "[error]" in result:
                break
            results.append(result)
        _close_run(run_id=actual_run_id, reason="complete")
        return f"[reading] Run {actual_run_id} foreground complete. {len(results)} items processed."
    else:
        # Background: spawn worker subprocess
        repo = Path(__file__).parent.parent.parent.parent
        worker = repo / "claudecode" / "reading_worker.py"
        if not worker.exists():
            _ensure_worker_script(worker, repo)

        python = repo / "venv" / "bin" / "python"
        subprocess.Popen(
            [str(python), str(worker), actual_run_id],
            stdout=open(paths().logs / "reading_worker.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return f"[reading] Run {actual_run_id} started in background."


def _ensure_worker_script(worker_path: Path, repo: Path) -> None:
    """Create the background worker script."""
    from ..paths import paths as _paths

    instance_dir = str(_paths().instance)
    worker_path.write_text(f'''#!/usr/bin/env python3
"""reading_worker.py — Background worker for reading runs."""
import sys, os
sys.path.insert(0, "{repo}")
sys.path.insert(0, "{repo / 'wild_igor'}")

_instance_dir = "{instance_dir}"
try:
    sys.path.insert(0, "{repo / 'wild_igor' / 'setup_assets'}")
    from installer import load_cfg
    load_cfg(_instance_dir)
except Exception:
    env_path = os.path.join(_instance_dir, ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

from igor.tools.reading_tool import _process_next, _close_run
import time

run_id = sys.argv[1]
print(f"Worker starting for {{run_id}}")
while True:
    result = _process_next(run_id=run_id)
    if "[no more items]" in result:
        break
    if "[error]" in result:
        print(f"Error: {{result}}")
        break
    print(result)
    time.sleep(2)

_close_run(run_id=run_id, reason="complete")
print(f"Worker done for {{run_id}}")
''')


def _process_next(run_id: str = "", **_kw) -> str:
    """Claim and process the next pending item in a run.

    Coordination (atomic claim) happens on reading_list table.
    Record (what happened) goes into Memory nodes.
    """
    if not run_id:
        return "[reading] [error] run_id required"

    from .reading_engine import append_run_log, fetch_to_blob, process_blob

    run_node_id = f"READING_{run_id}" if not run_id.startswith("READING_") else run_id
    cx = _cortex()
    run_mem = cx.get(run_node_id)
    if not run_mem:
        return f"[reading] [error] Run {run_id} not found"

    run_meta = run_mem.metadata or {}
    pass_number = run_meta.get("pass_number", 1)
    actual_run_id = run_meta.get("run_id", run_id)
    instance = _instance_id()

    # Atomic claim on reading_list (coordination table)
    with _db()() as conn:
        row = conn.execute(
            "UPDATE reading_list "
            "SET status='processing', claimed_by=%s "
            "WHERE source = ("
            "  SELECT source FROM reading_list"
            "  WHERE run_id=%s AND status='queued'"
            "  ORDER BY encoding_arousal DESC LIMIT 1"
            "  FOR UPDATE SKIP LOCKED"
            ") RETURNING source, title, author",
            (instance, actual_run_id),
        ).fetchone()

    if not row:
        return f"[reading] [no more items] Run {actual_run_id} has no pending items"

    source = row["source"]
    title = row["title"] or ""
    author = row["author"] or ""

    # Find the corresponding item Memory node
    item_node_id = None
    with _db()() as conn:
        item_rows = conn.execute(
            "SELECT id, metadata FROM memories "
            "WHERE id LIKE %s AND context_of_encoding = 'reading_run|item'"
            "AND metadata->>'source' = %s",
            (f"{run_node_id}_%", source),
        ).fetchall()
    if item_rows:
        item_node_id = item_rows[0]["id"]

    append_run_log(actual_run_id, f"FETCH  {source} ({instance})")

    # Phase 1: Fetch to blob
    try:
        blob_info = fetch_to_blob(source, title=title, author=author)
        bp = blob_info["blob_path"]

        # Update item Memory node
        if item_node_id:
            item_mem = cx.get(item_node_id)
            if item_mem:
                item_mem.metadata["status"] = "fetched"
                item_mem.metadata["blob_path"] = bp
                item_mem.metadata["claimed_by"] = instance
                item_mem.metadata["claimed_at"] = _now_iso()
                cx.store(item_mem)

        append_run_log(
            actual_run_id,
            f"FETCHED {source} → blob {Path(bp).stem} "
            f"({blob_info['total_sentences']} sentences)",
        )
    except Exception as e:
        # Mark failed in both coordination table and memory
        with _db()() as conn:
            conn.execute(
                "UPDATE reading_list SET status='failed' WHERE source=%s AND run_id=%s",
                (source, actual_run_id),
            )
        if item_node_id:
            item_mem = cx.get(item_node_id)
            if item_mem:
                item_mem.metadata["status"] = "failed"
                item_mem.metadata["error_message"] = str(e)[:500]
                cx.store(item_mem)
        append_run_log(actual_run_id, f"FAIL-FETCH {source}: {e}")
        return f"[reading] [error] Fetch failed for {source}: {e}"

    # Phase 2: Process from blob
    try:
        if item_node_id:
            item_mem = cx.get(item_node_id)
            if item_mem:
                item_mem.metadata["status"] = "processing"
                cx.store(item_mem)

        append_run_log(actual_run_id, f'START  {source} "{title}" ({instance})')

        result = process_blob(bp, pass_number=pass_number, use_local=True)

        # Update item Memory node with results
        if item_node_id:
            item_mem = cx.get(item_node_id)
            if item_mem:
                item_mem.metadata["status"] = "complete"
                item_mem.metadata["node_count"] = result["node_count"]
                item_mem.metadata["edge_count"] = result["edge_count"]
                item_mem.metadata["model_used"] = result["model_used"]
                item_mem.metadata["processing_seconds"] = result["processing_seconds"]
                item_mem.metadata["completed_at"] = _now_iso()
                item_mem.narrative = (
                    f"Read: {title or source}"
                    f"{' by ' + author if author else ''}"
                    f" — {result['node_count']} nodes, {result['processing_seconds']}s"
                    f" ({result['model_used']})"
                )
                cx.store(item_mem)

        # Update coordination table
        with _db()() as conn:
            conn.execute(
                "UPDATE reading_list SET status='completed', completed_at=%s "
                "WHERE source=%s AND run_id=%s",
                (_now_iso(), source, actual_run_id),
            )

        append_run_log(
            actual_run_id,
            f"DONE   {source}  nodes={result['node_count']} "
            f"edges={result['edge_count']} model={result['model_used']} "
            f"({result['processing_seconds']}s)",
        )
        return (
            f"[reading] Processed {title or source}: "
            f"{result['node_count']} nodes in {result['processing_seconds']}s"
        )

    except Exception as e:
        with _db()() as conn:
            conn.execute(
                "UPDATE reading_list SET status='failed' WHERE source=%s AND run_id=%s",
                (source, actual_run_id),
            )
        if item_node_id:
            item_mem = cx.get(item_node_id)
            if item_mem:
                item_mem.metadata["status"] = "failed"
                item_mem.metadata["error_message"] = str(e)[:500]
                cx.store(item_mem)
        append_run_log(actual_run_id, f"FAIL   {source}: {e}")
        return f"[reading] [error] Process failed for {source}: {e}"


def _read_now(source: str = "", title: str = "", **_kw) -> str:
    """Foreground interactive read: one book, synchronous."""
    if not source:
        return "[reading] source required (calibre://ID, URL, or title)"

    safe_label = (title or source)[:30].replace(" ", "-")
    result = _create_run(
        label=f"fg-{safe_label}", items=source, source_filter="completed", limit=1
    )
    if "No items" in result:
        # Try pending too
        result = _create_run(
            label=f"fg-{safe_label}", items=source, source_filter="pending", limit=1
        )
    if "No items" in result:
        return f"[reading] Could not find {source} in reading_list"

    # Extract run_id from result
    run_id = result.split("Run ")[1].split(" ")[0] if "Run " in result else None
    if not run_id:
        return result

    return _start_run(run_id=run_id, mode="foreground")


def _master_index(limit: int = 50, **_kw) -> str:
    """All books ever read — from memory graph + legacy reading_list."""
    with _db()() as conn:
        # Run items from memory graph (completed items)
        new_rows = conn.execute(
            "SELECT id, narrative, metadata FROM memories "
            "WHERE id LIKE %s AND context_of_encoding = 'reading_run|item' "
            "AND metadata->>'status' = 'complete' "
            "ORDER BY metadata->>'completed_at' DESC NULLS LAST LIMIT %s",
            ("READING_RUN_%", limit),
        ).fetchall()

        # Legacy: reading_list completed
        old_rows = conn.execute(
            "SELECT title, author, completed_at, source "
            "FROM reading_list WHERE status='completed' "
            "ORDER BY completed_at DESC NULLS LAST LIMIT %s",
            (limit,),
        ).fetchall()

    lines = [f"MASTER READING INDEX (up to {limit} most recent):"]

    if new_rows:
        lines.append("\n-- Via reading runs (memory graph) --")
        for row in new_rows:
            meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
            title = meta.get("title", meta.get("source", "?"))
            author = meta.get("author", "")
            nodes = meta.get("node_count", 0)
            run_id = meta.get("run_id", "")
            line = f"  {title}"
            if author:
                line += f" — {author}"
            if nodes:
                line += f" ({nodes} nodes)"
            if run_id:
                line += f" [{run_id}]"
            lines.append(line)

    if old_rows:
        lines.append(f"\n-- Via legacy pipeline ({len(old_rows)} shown) --")
        for row in old_rows[:20]:
            line = f"  {row['title'] or row['source']}"
            if row["author"]:
                line += f" — {row['author']}"
            lines.append(line)
        if len(old_rows) > 20:
            lines.append(f"  ... and {len(old_rows) - 20} more")

    with _db()() as conn:
        total_legacy = conn.execute(
            "SELECT COUNT(*) AS cnt FROM reading_list WHERE status='completed'"
        ).fetchone()["cnt"]
    total_new = len(new_rows)
    lines.append(f"\nTotal: {total_legacy} legacy + {total_new} via runs")

    return "\n".join(lines)


def _run_log(run_id: str = "", **_kw) -> str:
    """Read the log file for a run."""
    if not run_id:
        return "[reading] run_id required"
    log_path = paths().reading_run_logs / f"{run_id}.log"
    if not log_path.exists():
        return f"[reading] No log file for {run_id}"
    return log_path.read_text()


def _my_reading(**_kw) -> str:
    """Self-visibility: what am I reading? Active runs, my items, pipeline health."""
    instance = _instance_id()

    with _db()() as conn:
        # Active runs from memory graph
        active = conn.execute(
            "SELECT id, metadata FROM memories "
            "WHERE id LIKE %s "
            "AND context_of_encoding = 'reading_run|created' "
            "AND metadata->>'status' = 'active' "
            "ORDER BY metadata->>'started_at' DESC",
            ("READING_RUN_%",),
        ).fetchall()

        # Items I've claimed (from memory graph)
        my_items = conn.execute(
            "SELECT id, metadata FROM memories "
            "WHERE id LIKE %s AND context_of_encoding = 'reading_run|item' "
            "AND metadata->>'claimed_by' = %s "
            "AND metadata->>'status' IN ('fetching', 'fetched', 'processing') "
            "ORDER BY metadata->>'claimed_at' DESC LIMIT 5",
            ("READING_RUN_%", instance),
        ).fetchall()

        # Pipeline health
        pending_total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM reading_list WHERE status='pending'"
        ).fetchone()["cnt"]
        completed_total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM reading_list WHERE status='completed'"
        ).fetchone()["cnt"]

    lines = [f"MY READING STATUS ({instance}):"]

    if active:
        lines.append("\nActive runs:")
        for row in active:
            meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
            lines.append(
                f"  {meta.get('run_id', row['id'])} — {meta.get('label', '')} "
                f"(pass {meta.get('pass_number', 1)}, started {meta.get('started_at', '?')})"
            )
    else:
        lines.append("\nNo active runs.")

    if my_items:
        lines.append("\nItems I'm working on:")
        for row in my_items:
            meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
            lines.append(
                f"  [{meta.get('status', '?')}] {meta.get('title', meta.get('source', '?'))}"
            )
    else:
        lines.append("\nNot currently processing any items.")

    lines.append(f"\nPipeline: {pending_total} pending, {completed_total} completed")

    return "\n".join(lines)


# ── Command dispatch ─────────────────────────────────────────────────────────

_COMMANDS = {
    "create_run": _create_run,
    "list_runs": _list_runs,
    "run_status": _run_status,
    "close_run": _close_run,
    "start_run": _start_run,
    "process_next": _process_next,
    "read_now": _read_now,
    "master_index": _master_index,
    "run_log": _run_log,
    "my_reading": _my_reading,
}


def reading(command: str = "", **kwargs) -> str:
    """Unified reading tool — one tool, two modes."""
    if not command:
        return (
            "[reading] Commands: create_run, list_runs, run_status, close_run, "
            "start_run, process_next, read_now, master_index, run_log, my_reading"
        )
    fn = _COMMANDS.get(command)
    if not fn:
        return (
            f"[reading] Unknown command: {command}. Available: {', '.join(_COMMANDS)}"
        )
    try:
        return fn(**kwargs)
    except Exception as e:
        return f"[reading] Error in {command}: {e}"


# ── Registration ─────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="reading",
        description=(
            "Unified reading tool. Commands: create_run (batch books into a run), "
            "list_runs (show all runs), run_status (detailed run info), "
            "close_run (seal a run), start_run (begin processing, mode=foreground|background), "
            "read_now (interactive single-book read), master_index (all books ever read), "
            "run_log (combined slate+log), my_reading (self-visibility). "
            "One tool, two modes: foreground (interactive) and background (batch)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Subcommand name"},
                "run_id": {"type": "string", "description": "Run ID"},
                "label": {"type": "string", "description": "Human label for a run"},
                "source": {
                    "type": "string",
                    "description": "Book source (calibre://ID, URL, or title)",
                },
                "title": {"type": "string", "description": "Book title"},
                "mode": {"type": "string", "description": "foreground or background"},
                "status": {"type": "string", "description": "Filter by status"},
                "reason": {"type": "string", "description": "Reason for closing a run"},
                "items": {
                    "type": "string",
                    "description": "Comma-separated source list",
                },
                "limit": {"type": "integer", "description": "Max items"},
                "pass_number": {
                    "type": "integer",
                    "description": "1=general, 2=situated (D333)",
                },
            },
            "required": ["command"],
        },
        fn=reading,
    )
)

"""
OS primitive tools — PRIM_LIST_DIR, PRIM_FILE_META, PRIM_READ_HEAD,
PRIM_TYPE_DETECT, PRIM_ITER_NEXT, PRIM_ITER_DONE  (T-os-primitives).

Six reusable habits that compose "for each file, do X" loops entirely from
habits already in the graph. Each primitive reads its inputs from the active
traversal context and writes outputs back. The context_id flows through the
chain via the traversal_contexts table — the most recently started context
is used (single-chain assumption; concurrent chains are future work).

Context key conventions:
  dir            — directory path to scan (input for PRIM_LIST_DIR)
  files          — JSON list of file paths (written by PRIM_LIST_DIR, consumed by ITER_*)
  current_file   — path of the file currently being processed
  content        — text content read by PRIM_READ_HEAD
  file_mtime     — ISO timestamp of last modification
  file_size      — file size in bytes (as string)
  file_type      — detected file type string (e.g. 'python', 'markdown', 'text', 'binary')
  read_head_lines— how many lines to read (optional; default 40)
  done           — 'true' when files list is exhausted

All tools registered with 0 required args — they auto-dispatch from action habits.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .registry import Tool, registry

# ── shared helpers ─────────────────────────────────────────────────────────────


def _get_cortex():
    db_path = os.getenv("IGOR_DB_PATH", "")
    if not db_path:
        from ..paths import paths

        db_path = str(paths().instance / "wild-0001.db")
    from ..memory.cortex import Cortex

    return Cortex(Path(db_path))


def _current_ctx_id(cortex) -> str | None:
    """Return the most recently started traversal context_id, or None."""
    try:
        with cortex._conn() as conn:
            row = conn.execute(
                "SELECT context_id FROM traversal_contexts "
                "WHERE key = '__init__' ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _ctx_get(cortex, ctx_id: str, key: str) -> str | None:
    return cortex.traversal_get(ctx_id, key)


def _ctx_set(cortex, ctx_id: str, key: str, value: str, step: int = 0) -> None:
    cortex.traversal_set(ctx_id, key, value, step=step)


# ── PRIM_LIST_DIR ──────────────────────────────────────────────────────────────


def prim_list_dir() -> str:
    """List files in ctx[dir], write JSON file list to ctx[files].

    Reads:  ctx[dir]
    Writes: ctx[files]  (JSON list of absolute path strings, sorted)
    """
    cortex = _get_cortex()
    ctx_id = _current_ctx_id(cortex)
    if not ctx_id:
        return "[PRIM_LIST_DIR] no active traversal context"
    directory = _ctx_get(cortex, ctx_id, "dir")
    if not directory:
        return "[PRIM_LIST_DIR] ctx[dir] not set"
    try:
        p = Path(directory).expanduser().resolve()
        if not p.is_dir():
            return f"[PRIM_LIST_DIR] not a directory: {directory}"
        files = sorted(str(f) for f in p.iterdir() if f.is_file())
        _ctx_set(cortex, ctx_id, "files", json.dumps(files), step=0)
        return f"PRIM_LIST_DIR: {len(files)} files in {directory}"
    except Exception as e:
        return f"[PRIM_LIST_DIR] error: {e}"


# ── PRIM_FILE_META ─────────────────────────────────────────────────────────────


def prim_file_meta() -> str:
    """Get mtime + size for ctx[current_file], write to ctx[file_mtime] + ctx[file_size].

    Reads:  ctx[current_file]
    Writes: ctx[file_mtime]  (ISO timestamp string)
            ctx[file_size]   (bytes as decimal string)
    """
    cortex = _get_cortex()
    ctx_id = _current_ctx_id(cortex)
    if not ctx_id:
        return "[PRIM_FILE_META] no active traversal context"
    filepath = _ctx_get(cortex, ctx_id, "current_file")
    if not filepath:
        return "[PRIM_FILE_META] ctx[current_file] not set"
    try:
        p = Path(filepath)
        s = p.stat()
        from datetime import datetime, timezone

        mtime = datetime.fromtimestamp(s.st_mtime, tz=timezone.utc).isoformat()
        _ctx_set(cortex, ctx_id, "file_mtime", mtime, step=0)
        _ctx_set(cortex, ctx_id, "file_size", str(s.st_size), step=0)
        return f"PRIM_FILE_META: {p.name} — {s.st_size}B, mtime={mtime}"
    except Exception as e:
        return f"[PRIM_FILE_META] error: {e}"


# ── PRIM_READ_HEAD ─────────────────────────────────────────────────────────────


def prim_read_head() -> str:
    """Read first N lines of ctx[current_file], write to ctx[content].

    Reads:  ctx[current_file]
            ctx[read_head_lines]  (optional; default 40)
    Writes: ctx[content]
    """
    cortex = _get_cortex()
    ctx_id = _current_ctx_id(cortex)
    if not ctx_id:
        return "[PRIM_READ_HEAD] no active traversal context"
    filepath = _ctx_get(cortex, ctx_id, "current_file")
    if not filepath:
        return "[PRIM_READ_HEAD] ctx[current_file] not set"
    try:
        n_raw = _ctx_get(cortex, ctx_id, "read_head_lines")
        n = int(n_raw) if n_raw and n_raw.isdigit() else 40
        p = Path(filepath)
        lines = []
        with p.open("r", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= n:
                    break
                lines.append(line.rstrip("\n"))
        content = "\n".join(lines)
        _ctx_set(cortex, ctx_id, "content", content, step=0)
        return f"PRIM_READ_HEAD: read {len(lines)} lines from {p.name}"
    except Exception as e:
        return f"[PRIM_READ_HEAD] error: {e}"


# ── PRIM_TYPE_DETECT ───────────────────────────────────────────────────────────

_EXT_MAP = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".md": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "shell",
    ".bash": "shell",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".sql": "sql",
    ".db": "sqlite",
    ".pdf": "pdf",
    ".epub": "epub",
    ".mobi": "ebook",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".svg": "image",
    ".zip": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".dsb": "dsb",
    ".csb": "csb",
}


def prim_type_detect() -> str:
    """Detect file type for ctx[current_file], write to ctx[file_type].

    Reads:  ctx[current_file]
    Writes: ctx[file_type]  (e.g. 'python', 'markdown', 'binary', 'text')
    """
    cortex = _get_cortex()
    ctx_id = _current_ctx_id(cortex)
    if not ctx_id:
        return "[PRIM_TYPE_DETECT] no active traversal context"
    filepath = _ctx_get(cortex, ctx_id, "current_file")
    if not filepath:
        return "[PRIM_TYPE_DETECT] ctx[current_file] not set"
    try:
        p = Path(filepath)
        # Extension lookup first
        ftype = _EXT_MAP.get(p.suffix.lower())
        if not ftype:
            # Sniff first 512 bytes for null bytes → binary
            with p.open("rb") as fh:
                chunk = fh.read(512)
            ftype = "binary" if b"\x00" in chunk else "text"
        _ctx_set(cortex, ctx_id, "file_type", ftype, step=0)
        return f"PRIM_TYPE_DETECT: {p.name} → {ftype}"
    except Exception as e:
        return f"[PRIM_TYPE_DETECT] error: {e}"


# ── PRIM_ITER_NEXT ─────────────────────────────────────────────────────────────


def prim_iter_next() -> str:
    """Pop first item from ctx[files] into ctx[current_file], update ctx[files].

    Reads:  ctx[files]
    Writes: ctx[current_file]  (next file path)
            ctx[files]         (remaining list, JSON)
    Returns error string if ctx[files] is empty.
    """
    cortex = _get_cortex()
    ctx_id = _current_ctx_id(cortex)
    if not ctx_id:
        return "[PRIM_ITER_NEXT] no active traversal context"
    files_raw = _ctx_get(cortex, ctx_id, "files")
    if not files_raw:
        return "[PRIM_ITER_NEXT] ctx[files] not set — run PRIM_LIST_DIR first"
    try:
        files = json.loads(files_raw)
    except (json.JSONDecodeError, ValueError):
        return "[PRIM_ITER_NEXT] ctx[files] is not valid JSON"
    if not files:
        return "[PRIM_ITER_NEXT] ctx[files] is empty — iteration complete"
    current = files.pop(0)
    _ctx_set(cortex, ctx_id, "current_file", current, step=0)
    _ctx_set(cortex, ctx_id, "files", json.dumps(files), step=0)
    return f"PRIM_ITER_NEXT: current_file={Path(current).name} ({len(files)} remaining)"


# ── PRIM_ITER_DONE ─────────────────────────────────────────────────────────────


def prim_iter_done() -> str:
    """Check if ctx[files] is empty; write ctx[done]='true' or 'false'.

    Reads:  ctx[files]
    Writes: ctx[done]  ('true' if exhausted, 'false' otherwise)
    """
    cortex = _get_cortex()
    ctx_id = _current_ctx_id(cortex)
    if not ctx_id:
        return "[PRIM_ITER_DONE] no active traversal context"
    files_raw = _ctx_get(cortex, ctx_id, "files")
    try:
        files = json.loads(files_raw) if files_raw else []
    except (json.JSONDecodeError, ValueError):
        files = []
    done = "true" if not files else "false"
    _ctx_set(cortex, ctx_id, "done", done, step=0)
    return f"PRIM_ITER_DONE: done={done} ({len(files)} files remaining)"


# ── Tool registrations ─────────────────────────────────────────────────────────

_NO_ARGS = {"type": "object", "properties": {}, "required": []}

registry.register(
    Tool(
        name="prim_list_dir",
        description=(
            "OS primitive: list files in the directory at ctx[dir] and write the result "
            "to ctx[files] as a JSON list. Part of habit-chain iteration pattern."
        ),
        parameters=_NO_ARGS,
        fn=prim_list_dir,
    )
)

registry.register(
    Tool(
        name="prim_file_meta",
        description=(
            "OS primitive: get mtime and size for ctx[current_file]; "
            "write to ctx[file_mtime] and ctx[file_size]."
        ),
        parameters=_NO_ARGS,
        fn=prim_file_meta,
    )
)

registry.register(
    Tool(
        name="prim_read_head",
        description=(
            "OS primitive: read the first N lines (ctx[read_head_lines], default 40) "
            "of ctx[current_file]; write to ctx[content]."
        ),
        parameters=_NO_ARGS,
        fn=prim_read_head,
    )
)

registry.register(
    Tool(
        name="prim_type_detect",
        description=(
            "OS primitive: detect the file type of ctx[current_file] from extension "
            "and magic bytes; write result to ctx[file_type]."
        ),
        parameters=_NO_ARGS,
        fn=prim_type_detect,
    )
)

registry.register(
    Tool(
        name="prim_iter_next",
        description=(
            "OS primitive: pop the next path from ctx[files] into ctx[current_file] "
            "and update ctx[files] with the remainder."
        ),
        parameters=_NO_ARGS,
        fn=prim_iter_next,
    )
)

registry.register(
    Tool(
        name="prim_iter_done",
        description=(
            "OS primitive: check if ctx[files] is exhausted; "
            "write ctx[done]='true' or 'false'."
        ),
        parameters=_NO_ARGS,
        fn=prim_iter_done,
    )
)

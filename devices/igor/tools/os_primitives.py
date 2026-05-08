"""
OS primitive tools — PRIM_LIST_DIR, PRIM_FILE_META, PRIM_READ_HEAD,
PRIM_TYPE_DETECT, PRIM_ITER_NEXT, PRIM_ITER_DONE  (T-os-primitives);
PRIM_RING_READ (T-ring-read-tool).

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
import re
import stat
from pathlib import Path

from lab.utility_closet.registry import Tool, registry

# ── shared helpers ─────────────────────────────────────────────────────────────


def _get_cortex():
    from ..memory.cortex import Cortex

    return Cortex(None)


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


# ── PRIM_LIST_PUSH / PRIM_LIST_POP / PRIM_LIST_COUNT (D095 lists table) ────────


def prim_list_push() -> str:
    """Append an item to a named D095 list.

    Reads:  ctx[list_name]   — name of the list
            ctx[list_value]  — value to append (item_key = UUID timestamp)
    Writes: ctx[list_count]  — updated count after push
    """
    cortex = _get_cortex()
    ctx_id = _current_ctx_id(cortex)
    if not ctx_id:
        return "[PRIM_LIST_PUSH] no active traversal context"
    list_name = _ctx_get(cortex, ctx_id, "list_name")
    if not list_name:
        return "[PRIM_LIST_PUSH] ctx[list_name] not set"
    value = _ctx_get(cortex, ctx_id, "list_value") or ""
    import uuid as _uuid
    from datetime import datetime as _dt

    item_key = f"{_dt.now().isoformat()}_{_uuid.uuid4().hex[:8]}"
    now_iso = _dt.now().isoformat()
    try:
        with cortex._conn() as conn:
            conn.execute(
                "INSERT INTO lists (list_name, item_key, item_value, instance_id, updated_at) "
                "VALUES (?, %s, %s, %s, %s)",
                (list_name, item_key, value, "", now_iso),
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM lists WHERE list_name = %s AND instance_id = ''",
                (list_name,),
            ).fetchone()[0]
        _ctx_set(cortex, ctx_id, "list_count", str(count), step=0)
        return f"PRIM_LIST_PUSH: {list_name}[{item_key}]={value!r} (count={count})"
    except Exception as e:
        return f"[PRIM_LIST_PUSH] error: {e}"


def prim_list_pop() -> str:
    """Remove and return the oldest item from a named D095 list (FIFO).

    Reads:  ctx[list_name]   — name of the list
    Writes: ctx[list_value]  — value of the popped item (empty string if list was empty)
            ctx[list_key]    — item_key of the popped item
            ctx[list_count]  — remaining count after pop
    """
    cortex = _get_cortex()
    ctx_id = _current_ctx_id(cortex)
    if not ctx_id:
        return "[PRIM_LIST_POP] no active traversal context"
    list_name = _ctx_get(cortex, ctx_id, "list_name")
    if not list_name:
        return "[PRIM_LIST_POP] ctx[list_name] not set"
    try:
        with cortex._conn() as conn:
            row = conn.execute(
                "SELECT item_key, item_value FROM lists "
                "WHERE list_name = %s AND instance_id = '' "
                "ORDER BY updated_at ASC LIMIT 1",
                (list_name,),
            ).fetchone()
            if not row:
                _ctx_set(cortex, ctx_id, "list_value", "", step=0)
                _ctx_set(cortex, ctx_id, "list_key", "", step=0)
                _ctx_set(cortex, ctx_id, "list_count", "0", step=0)
                return f"PRIM_LIST_POP: {list_name} is empty"
            item_key, item_value = row[0], row[1] or ""
            conn.execute(
                "DELETE FROM lists WHERE list_name = %s AND item_key = %s AND instance_id = ''",
                (list_name, item_key),
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM lists WHERE list_name = %s AND instance_id = ''",
                (list_name,),
            ).fetchone()[0]
        _ctx_set(cortex, ctx_id, "list_value", item_value, step=0)
        _ctx_set(cortex, ctx_id, "list_key", item_key, step=0)
        _ctx_set(cortex, ctx_id, "list_count", str(count), step=0)
        return f"PRIM_LIST_POP: popped {list_name}[{item_key}]={item_value!r} ({count} remaining)"
    except Exception as e:
        return f"[PRIM_LIST_POP] error: {e}"


def prim_list_count() -> str:
    """Count items in a named D095 list.

    Reads:  ctx[list_name]   — name of the list
    Writes: ctx[list_count]  — current item count (as string)
    """
    cortex = _get_cortex()
    ctx_id = _current_ctx_id(cortex)
    if not ctx_id:
        return "[PRIM_LIST_COUNT] no active traversal context"
    list_name = _ctx_get(cortex, ctx_id, "list_name")
    if not list_name:
        return "[PRIM_LIST_COUNT] ctx[list_name] not set"
    try:
        with cortex._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM lists WHERE list_name = %s AND instance_id = ''",
                (list_name,),
            ).fetchone()[0]
        _ctx_set(cortex, ctx_id, "list_count", str(count), step=0)
        return f"PRIM_LIST_COUNT: {list_name} has {count} item(s)"
    except Exception as e:
        return f"[PRIM_LIST_COUNT] error: {e}"


# ── Node primitives (T-node-primitives) ────────────────────────────────────────


def prim_node_create() -> str:
    """Create or upsert a Memory node from context keys; write new node id to ctx[node_id].

    Required context keys:
      node_narrative — the memory's narrative text
      node_type      — MemoryType value string (e.g. 'FACTUAL', 'EPISODIC', 'INTERPRETIVE')
    Optional:
      node_id_key    — the id to use (default: auto-generated slug from first 40 chars)
      node_parent    — parent id to link to via add_child (default: 'CP1')
    """
    try:
        import uuid as _uuid
        from ..memory.models import Memory, MemoryType

        cortex = _get_cortex()
        ctx_id = _current_ctx_id(cortex)
        if not ctx_id:
            return "[PRIM_NODE_CREATE] no active context"
        narrative = _ctx_get(cortex, ctx_id, "node_narrative") or ""
        type_str = _ctx_get(cortex, ctx_id, "node_type") or "FACTUAL"
        node_id_key = _ctx_get(cortex, ctx_id, "node_id_key") or ""
        parent = _ctx_get(cortex, ctx_id, "node_parent") or "CP1"

        if not narrative:
            return "[PRIM_NODE_CREATE] node_narrative not set in context"

        # Auto-generate id if not provided
        if not node_id_key:
            slug = re.sub(r"[^a-z0-9]+", "_", narrative[:40].lower()).strip("_")
            node_id_key = f"AUTO_{slug}_{_uuid.uuid4().hex[:6].upper()}"

        try:
            mem_type = MemoryType[type_str.upper()]
        except KeyError:
            mem_type = MemoryType.FACTUAL

        existing = cortex.get(node_id_key)
        if existing:
            existing.narrative = narrative
            cortex.store(existing)
            action = "updated"
        else:
            mem = Memory(id=node_id_key, narrative=narrative, memory_type=mem_type)
            cortex.store(mem)
            cortex.add_child(parent, node_id_key)
            action = f"created → parent={parent}"

        _ctx_set(cortex, ctx_id, "node_id", node_id_key, step=0)
        return f"PRIM_NODE_CREATE: {node_id_key} {action}"
    except Exception as e:
        return f"[PRIM_NODE_CREATE] error: {e}"


def prim_node_link() -> str:
    """Link ctx[link_parent] → ctx[link_child] in the memory graph.

    ctx[link_parent] — parent node id (required)
    ctx[link_child]  — child node id (required)
    Safe to call if edge already exists.
    """
    try:
        cortex = _get_cortex()
        ctx_id = _current_ctx_id(cortex)
        if not ctx_id:
            return "[PRIM_NODE_LINK] no active context"
        parent = _ctx_get(cortex, ctx_id, "link_parent") or ""
        child = _ctx_get(cortex, ctx_id, "link_child") or ""
        if not parent or not child:
            return "[PRIM_NODE_LINK] link_parent and link_child required in context"
        cortex.add_child(parent, child)
        return f"PRIM_NODE_LINK: {parent} → {child}"
    except Exception as e:
        return f"[PRIM_NODE_LINK] error: {e}"


def prim_node_search() -> str:
    """Search memory graph from ctx[search_query], write results to ctx[search_results].

    ctx[search_query]  — search text (required)
    ctx[search_limit]  — max results (default 5)
    Writes newline-joined 'id|type|narrative[:120]' lines to ctx[search_results].
    Writes match count to ctx[search_count].
    Also writes ctx[node_id] = id of top result (for chaining).
    """
    try:
        cortex = _get_cortex()
        ctx_id = _current_ctx_id(cortex)
        if not ctx_id:
            return "[PRIM_NODE_SEARCH] no active context"
        query = _ctx_get(cortex, ctx_id, "search_query") or ""
        if not query:
            return "[PRIM_NODE_SEARCH] search_query not set in context"
        limit_raw = _ctx_get(cortex, ctx_id, "search_limit") or "5"
        try:
            limit = int(limit_raw)
        except ValueError:
            limit = 5

        results = cortex.search(query, limit=limit)
        if not results:
            _ctx_set(cortex, ctx_id, "search_results", "", step=0)
            _ctx_set(cortex, ctx_id, "search_count", "0", step=0)
            return "PRIM_NODE_SEARCH: no results"

        lines = []
        for mem in results:
            snippet = mem.narrative[:120].replace("\n", " ")
            lines.append(f"{mem.id}|{mem.memory_type.value}|{snippet}")

        _ctx_set(cortex, ctx_id, "search_results", "\n".join(lines), step=0)
        _ctx_set(cortex, ctx_id, "search_count", str(len(lines)), step=0)
        _ctx_set(cortex, ctx_id, "node_id", results[0].id, step=0)
        return f"PRIM_NODE_SEARCH: {len(lines)} result(s) for '{query[:40]}'"
    except Exception as e:
        return f"[PRIM_NODE_SEARCH] error: {e}"


def prim_twm_push() -> str:
    """Push a TWM observation from context keys; write new obs id to ctx[twm_obs_id].

    ctx[twm_content]  — content_csb string (required)
    ctx[twm_source]   — source label (default 'habit_chain')
    ctx[twm_salience] — float 0-1 (default 0.5)
    ctx[twm_urgency]  — float 0-1 (default 0.3)
    ctx[twm_ttl]      — TTL in seconds (default 300)
    """
    try:
        cortex = _get_cortex()
        ctx_id = _current_ctx_id(cortex)
        if not ctx_id:
            return "[PRIM_TWM_PUSH] no active context"
        content = _ctx_get(cortex, ctx_id, "twm_content") or ""
        if not content:
            return "[PRIM_TWM_PUSH] twm_content not set in context"
        source = _ctx_get(cortex, ctx_id, "twm_source") or "habit_chain"
        try:
            salience = float(_ctx_get(cortex, ctx_id, "twm_salience") or "0.5")
        except ValueError:
            salience = 0.5
        try:
            urgency = float(_ctx_get(cortex, ctx_id, "twm_urgency") or "0.3")
        except ValueError:
            urgency = 0.3
        try:
            ttl = int(_ctx_get(cortex, ctx_id, "twm_ttl") or "300")
        except ValueError:
            ttl = 300

        obs_id = cortex.twm_push(
            source=source,
            content_csb=content,
            salience=salience,
            urgency=urgency,
            ttl_seconds=ttl,
        )
        _ctx_set(cortex, ctx_id, "twm_obs_id", str(obs_id), step=0)
        return (
            f"PRIM_TWM_PUSH: pushed obs_id={obs_id} salience={salience} source={source}"
        )
    except Exception as e:
        return f"[PRIM_TWM_PUSH] error: {e}"


# ── String primitives (T-string-primitives) ────────────────────────────────────


def prim_str_split() -> str:
    """Split ctx[content] on ctx[split_sep] (default '\\n'), write list to ctx[split_parts] and count to ctx[split_count].

    ctx[split_sep]   — separator string (default newline)
    ctx[split_maxn]  — max splits (default 0 = unlimited)
    """
    try:
        cortex = _get_cortex()
        ctx_id = _current_ctx_id(cortex)
        if not ctx_id:
            return "[PRIM_STR_SPLIT] no active context"
        text = _ctx_get(cortex, ctx_id, "content") or ""
        sep = _ctx_get(cortex, ctx_id, "split_sep") or "\n"
        maxn_raw = _ctx_get(cortex, ctx_id, "split_maxn") or "0"
        maxn = int(maxn_raw) if str(maxn_raw).isdigit() else 0
        # Handle common escape sequences
        sep = sep.replace("\\n", "\n").replace("\\t", "\t")
        parts = text.split(sep, maxn) if maxn > 0 else text.split(sep)
        _ctx_set(cortex, ctx_id, "split_parts", __import__("json").dumps(parts), step=0)
        _ctx_set(cortex, ctx_id, "split_count", str(len(parts)), step=0)
        return f"PRIM_STR_SPLIT: split into {len(parts)} part(s)"
    except Exception as e:
        return f"[PRIM_STR_SPLIT] error: {e}"


def prim_str_regex() -> str:
    """Search ctx[content] with regex pattern ctx[regex_pattern], write first match to ctx[regex_match].

    ctx[regex_pattern] — regex pattern (required)
    ctx[regex_group]   — capture group index/name (default 0 = whole match)
    ctx[regex_flags]   — flags string: 'i'=ignorecase, 'm'=multiline, 's'=dotall
    Writes ctx[regex_matched]='true'/'false'.
    """
    try:
        cortex = _get_cortex()
        ctx_id = _current_ctx_id(cortex)
        if not ctx_id:
            return "[PRIM_STR_REGEX] no active context"
        text = _ctx_get(cortex, ctx_id, "content") or ""
        pattern = _ctx_get(cortex, ctx_id, "regex_pattern") or ""
        if not pattern:
            return "[PRIM_STR_REGEX] regex_pattern not set in context"
        group_raw = _ctx_get(cortex, ctx_id, "regex_group") or "0"
        flags_str = _ctx_get(cortex, ctx_id, "regex_flags") or ""
        flags = 0
        if "i" in flags_str:
            flags |= re.IGNORECASE
        if "m" in flags_str:
            flags |= re.MULTILINE
        if "s" in flags_str:
            flags |= re.DOTALL
        m = re.search(pattern, text, flags)
        if m:
            try:
                group = int(group_raw)
            except ValueError:
                group = group_raw  # named group
            match_val = m.group(group)
            _ctx_set(cortex, ctx_id, "regex_match", match_val, step=0)
            _ctx_set(cortex, ctx_id, "regex_matched", "true", step=0)
            return f"PRIM_STR_REGEX: matched '{match_val[:80]}'"
        else:
            _ctx_set(cortex, ctx_id, "regex_match", "", step=0)
            _ctx_set(cortex, ctx_id, "regex_matched", "false", step=0)
            return "PRIM_STR_REGEX: no match"
    except Exception as e:
        return f"[PRIM_STR_REGEX] error: {e}"


def prim_str_format() -> str:
    """Format ctx[format_template] substituting {key} references from context, write to ctx[format_result].

    ctx[format_template] — template string with {key} placeholders
    All other context keys are available as substitution values.
    Missing keys leave the placeholder as-is (no KeyError).
    """
    try:
        cortex = _get_cortex()
        ctx_id = _current_ctx_id(cortex)
        if not ctx_id:
            return "[PRIM_STR_FORMAT] no active context"
        template = _ctx_get(cortex, ctx_id, "format_template") or ""
        if not template:
            return "[PRIM_STR_FORMAT] format_template not set in context"
        # Load all context keys for substitution
        with cortex._local_conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM traversal_contexts WHERE context_id = %s",
                (ctx_id,),
            ).fetchall()
        ctx_dict = {r["key"]: (r["value"] or "") for r in rows}
        # Safe format — missing keys stay as {key}
        result = re.sub(
            r"\{(\w+)\}",
            lambda mo: ctx_dict.get(mo.group(1), mo.group(0)),
            template,
        )
        _ctx_set(cortex, ctx_id, "format_result", result, step=0)
        return f"PRIM_STR_FORMAT: formatted ({len(result)} chars)"
    except Exception as e:
        return f"[PRIM_STR_FORMAT] error: {e}"


def prim_str_slice() -> str:
    """Slice ctx[content] from ctx[slice_start] to ctx[slice_end], write to ctx[slice_result].

    ctx[slice_start] — start index (default 0); negative = from end
    ctx[slice_end]   — end index (default = end of string); negative = from end
    Also useful for truncation: set slice_end to max chars wanted.
    """
    try:
        cortex = _get_cortex()
        ctx_id = _current_ctx_id(cortex)
        if not ctx_id:
            return "[PRIM_STR_SLICE] no active context"
        text = _ctx_get(cortex, ctx_id, "content") or ""
        start_raw = _ctx_get(cortex, ctx_id, "slice_start") or "0"
        end_raw = _ctx_get(cortex, ctx_id, "slice_end") or ""
        try:
            start = int(start_raw)
        except ValueError:
            start = 0
        if end_raw:
            try:
                end = int(end_raw)
                result = text[start:end]
            except ValueError:
                result = text[start:]
        else:
            result = text[start:]
        _ctx_set(cortex, ctx_id, "slice_result", result, step=0)
        return (
            f"PRIM_STR_SLICE: slice [{start}:{end_raw or 'end'}] → {len(result)} chars"
        )
    except Exception as e:
        return f"[PRIM_STR_SLICE] error: {e}"


def prim_twm_read() -> str:
    """Read active TWM observations; write formatted summary to ctx[twm_items].

    Calls cortex.twm_read(include_integrated=False, limit=50).
    Entries grouped by salience (sorted desc). Each item formatted as:
      [{category}|sal={salience:.2f}|urg={urgency:.2f}] {content snippet}
    Writes newline-joined string to ctx[twm_items] and item count to ctx[twm_count].
    NE impulses (ACTION_IMPULSE| prefix) are included — they live in TWM.
    """
    try:
        cortex = _get_cortex()
        items = cortex.twm_read(include_integrated=False, limit=50)
        if not items:
            ctx_id = _current_ctx_id(cortex)
            if ctx_id:
                _ctx_set(cortex, ctx_id, "twm_items", "(empty)", step=0)
                _ctx_set(cortex, ctx_id, "twm_count", "0", step=0)
            return "PRIM_TWM_READ: TWM is empty (no active observations)"

        items_sorted = sorted(items, key=lambda r: r.get("salience", 0.0), reverse=True)
        lines = []
        for r in items_sorted:
            cat = r.get("category", "observation")
            sal = r.get("salience", 0.0)
            urg = r.get("urgency", 0.5)
            content = r.get("content_csb", "")
            snippet = content[:120] + "…" if len(content) > 120 else content
            lines.append(f"[{cat}|sal={sal:.2f}|urg={urg:.2f}] {snippet}")

        summary = "\n".join(lines)
        ctx_id = _current_ctx_id(cortex)
        if ctx_id:
            _ctx_set(cortex, ctx_id, "twm_items", summary, step=0)
            _ctx_set(cortex, ctx_id, "twm_count", str(len(lines)), step=0)
        return f"PRIM_TWM_READ: {len(lines)} active item(s)\n{summary}"
    except Exception as e:
        return f"[PRIM_TWM_READ] error: {e}"


def prim_twm_read_active_goal() -> str:
    """
    Read the current ACTIVE_GOAL from TWM.
    Returns the goal text, or 'No active goal set.' if none.
    Lets Igor introspect what goal his working memory is currently holding.
    """
    try:
        cortex = _get_cortex()
        goal = cortex.twm_get_active_goal()
        if goal:
            return goal
        return "No active goal set."
    except Exception as e:
        return f"[PRIM_TWM_READ_ACTIVE_GOAL] error: {e}"


def prim_ring_read() -> str:
    """Read recent ring memory entries (IGOR_SAID, IGOR_HEARD, etc.).

    Reads ctx[ring_limit] (default 20) and ctx[ring_category] (optional filter).
    Returns entries in chronological order (oldest first).
    Writes formatted summary to ctx[ring_entries] and count to ctx[ring_count].

    Common categories: IGOR_SAID, IGOR_HEARD, restart_note, TWM_PULSE.
    Leave ring_category unset to read all categories.
    """
    try:
        cortex = _get_cortex()
        ctx_id = _current_ctx_id(cortex)

        limit = 20
        category = None
        if ctx_id:
            raw_limit = _ctx_get(cortex, ctx_id, "ring_limit")
            if raw_limit and raw_limit.isdigit():
                limit = int(raw_limit)
            raw_cat = _ctx_get(cortex, ctx_id, "ring_category")
            if raw_cat:
                category = raw_cat.strip()

        entries = cortex.read_ring_memory(limit=limit, category=category or None)
        if not entries:
            if ctx_id:
                _ctx_set(cortex, ctx_id, "ring_entries", "(empty)", step=0)
                _ctx_set(cortex, ctx_id, "ring_count", "0", step=0)
            cat_note = f" (category={category})" if category else ""
            return f"PRIM_RING_READ: ring memory is empty{cat_note}"

        lines = []
        for r in entries:
            cat = r.get("category", "")
            ts = r.get("timestamp", "")[:19]  # trim to seconds
            content = r.get("content", "")
            snippet = content[:160] + "…" if len(content) > 160 else content
            lines.append(f"[{cat}|{ts}] {snippet}")

        summary = "\n".join(lines)
        if ctx_id:
            _ctx_set(cortex, ctx_id, "ring_entries", summary, step=0)
            _ctx_set(cortex, ctx_id, "ring_count", str(len(lines)), step=0)
        cat_note = f" category={category}" if category else ""
        return f"PRIM_RING_READ: {len(lines)} entries{cat_note}\n{summary}"
    except Exception as e:
        return f"[PRIM_RING_READ] error: {e}"


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

registry.register(
    Tool(
        name="prim_list_push",
        description=(
            "D095 list primitive: append ctx[list_value] to the named list at ctx[list_name]. "
            "Writes updated count to ctx[list_count]."
        ),
        parameters=_NO_ARGS,
        fn=prim_list_push,
    )
)

registry.register(
    Tool(
        name="prim_list_pop",
        description=(
            "D095 list primitive: pop the oldest item from ctx[list_name] (FIFO). "
            "Writes popped value to ctx[list_value], key to ctx[list_key], "
            "remaining count to ctx[list_count]."
        ),
        parameters=_NO_ARGS,
        fn=prim_list_pop,
    )
)

registry.register(
    Tool(
        name="prim_list_count",
        description=(
            "D095 list primitive: count items in ctx[list_name]. "
            "Writes count to ctx[list_count]."
        ),
        parameters=_NO_ARGS,
        fn=prim_list_count,
    )
)

registry.register(
    Tool(
        name="prim_ring_read",
        description=(
            "Ring memory primitive: read recent ring memory entries (IGOR_SAID, IGOR_HEARD, etc.). "
            "Reads ctx[ring_limit] (default 20) and ctx[ring_category] (optional filter). "
            "Writes formatted summary to ctx[ring_entries] (category|timestamp|content per line) "
            "and count to ctx[ring_count]. Use to inspect recent conversation history."
        ),
        parameters=_NO_ARGS,
        fn=prim_ring_read,
    )
)

registry.register(
    Tool(
        name="prim_twm_read",
        description=(
            "TWM primitive: read active (non-integrated) TWM observations. "
            "Writes formatted summary to ctx[twm_items] (salience|source|content per line, "
            "sorted by salience desc) and item count to ctx[twm_count]. "
            "Use for stew readout, affect check, and any habit that needs to inspect "
            "what is currently active in the transient working memory."
        ),
        parameters=_NO_ARGS,
        fn=prim_twm_read,
    )
)

registry.register(
    Tool(
        name="prim_node_create",
        description=(
            "Node primitive: create or upsert a Memory node from ctx[node_narrative] + "
            "ctx[node_type]. Optional ctx[node_id_key] (auto-generated if absent), "
            "ctx[node_parent] (default CP1). Writes new id to ctx[node_id]."
        ),
        parameters=_NO_ARGS,
        fn=prim_node_create,
    )
)

registry.register(
    Tool(
        name="prim_node_link",
        description=(
            "Node primitive: link ctx[link_parent] → ctx[link_child] in the memory graph. "
            "Safe to call if edge already exists."
        ),
        parameters=_NO_ARGS,
        fn=prim_node_link,
    )
)

registry.register(
    Tool(
        name="prim_node_search",
        description=(
            "Node primitive: search memory graph with ctx[search_query] (limit ctx[search_limit]). "
            "Writes results to ctx[search_results] (id|type|narrative lines), "
            "ctx[search_count], and ctx[node_id] = top result id."
        ),
        parameters=_NO_ARGS,
        fn=prim_node_search,
    )
)

registry.register(
    Tool(
        name="prim_twm_push",
        description=(
            "TWM primitive: push a TWM observation from context keys. "
            "ctx[twm_content] required; optional ctx[twm_source/salience/urgency/ttl]. "
            "Writes obs id to ctx[twm_obs_id]. Use to surface habit-chain results to pipeline."
        ),
        parameters=_NO_ARGS,
        fn=prim_twm_push,
    )
)

registry.register(
    Tool(
        name="prim_str_split",
        description=(
            "String primitive: split ctx[content] on ctx[split_sep] (default newline). "
            "Writes JSON list to ctx[split_parts] and count to ctx[split_count]. "
            "Optional ctx[split_maxn] limits number of splits."
        ),
        parameters=_NO_ARGS,
        fn=prim_str_split,
    )
)

registry.register(
    Tool(
        name="prim_str_regex",
        description=(
            "String primitive: search ctx[content] with regex ctx[regex_pattern]. "
            "Writes first match to ctx[regex_match]; ctx[regex_matched]='true'/'false'. "
            "Optional ctx[regex_group] (group index/name) and ctx[regex_flags] (i/m/s)."
        ),
        parameters=_NO_ARGS,
        fn=prim_str_regex,
    )
)

registry.register(
    Tool(
        name="prim_str_format",
        description=(
            "String primitive: expand ctx[format_template] with {key} substitutions "
            "from the active traversal context. Writes result to ctx[format_result]. "
            "Missing keys are left as-is. Use for building dynamic messages or CSB strings."
        ),
        parameters=_NO_ARGS,
        fn=prim_str_format,
    )
)

registry.register(
    Tool(
        name="prim_str_slice",
        description=(
            "String primitive: slice ctx[content] from ctx[slice_start] to ctx[slice_end]. "
            "Negative indices count from end. Writes result to ctx[slice_result]. "
            "Useful for truncation: set slice_end to max chars wanted."
        ),
        parameters=_NO_ARGS,
        fn=prim_str_slice,
    )
)

registry.register(
    Tool(
        name="prim_twm_read_active_goal",
        description=(
            "TWM primitive: read the current ACTIVE_GOAL from TWM. "
            "Returns the goal text, or 'No active goal set.' if none is held. "
            "Use for goal-aware introspection — lets a habit chain check what Igor "
            "is currently working on without a basket read."
        ),
        parameters=_NO_ARGS,
        fn=prim_twm_read_active_goal,
    )
)

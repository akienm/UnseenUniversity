#!/usr/bin/env python3
"""uurecall — multi-source recall: chat logs, tickets, code, palace, and semantic search.

Usage: uurecall.py <query words...>

Searches in order:
  1. CC.0 chat logs  — literal grep, most recent first, up to 5 hits
  2. Tickets         — id/title/description match against queue DB (all statuses)
  3. Code            — git grep in repo root, up to 5 lines
  4. Palace nodes    — Igor memory_palace + adc.palace literal match
  5. Semantic recall — librarian embedding search (suppressed below score 0.4)

Ticket IDs found across sources are de-duplicated: a ticket already shown
in the Tickets section is suppressed in Semantic results.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

_LOG_DIR = Path.home() / ".unseen_university" / "logs" / "CC.0"
_REPO_ROOT = Path(__file__).parent.parent.parent
_CC_TOOLS = Path(os.environ.get("CC_WORKFLOW_TOOLS", str(Path(__file__).parent)))
_CAP = 5

_TICKET_RE = re.compile(r'\bT-[\w-]+')
_SEMANTIC_THRESHOLD = 0.4


def _divider(label: str) -> None:
    print(f"\n━━ {label} ━━")


def _search_logs(query: str) -> set[str]:
    """Literal grep through CC.0 chat logs. Returns ticket IDs seen in hits."""
    mentioned: set[str] = set()
    if not _LOG_DIR.exists():
        _divider("CC.0 logs")
        print("  (log dir not found)")
        return mentioned

    logs = sorted(_LOG_DIR.glob("*.md"), reverse=True)[:20]
    ql = query.lower()
    hits_shown = 0
    header_printed = False

    for log_path in logs:
        if hits_shown >= _CAP:
            break
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        file_hits = [(i, line) for i, line in enumerate(lines) if ql in line.lower()]
        if not file_hits:
            continue

        if not header_printed:
            _divider("CC.0 logs")
            header_printed = True

        print(f"[{log_path.stem}] {len(file_hits)} hit(s):")
        for lineno, line in file_hits[:2]:
            for tid in _TICKET_RE.findall(line):
                mentioned.add(tid)
            ctx_before = lines[lineno - 1] if lineno > 0 else ""
            print(f"    {ctx_before[:120]}" if ctx_before.strip() else "", end="")
            if ctx_before.strip():
                print()
            print(f"  > {line[:120]}")
            hits_shown += 1
        print()

    if not header_printed:
        _divider("CC.0 logs")
        print("  (no literal hits)")

    return mentioned


def _search_tickets(query: str, log_ids: set[str]) -> set[str]:
    """Search queue DB tickets by id/title/description. Returns shown ticket IDs."""
    shown: set[str] = set()
    ql = query.lower()

    try:
        sys.path.insert(0, str(_CC_TOOLS))
        import cc_queue
        tasks = cc_queue.load_tasks()
    except Exception as e:
        _divider("Tickets")
        print(f"  (queue unavailable: {e})")
        return shown

    def _score(t: dict) -> int:
        tid = t.get("id", "")
        title = t.get("title", "")
        desc = t.get("description", "")
        if ql == tid.lower():
            return 0
        if ql in tid.lower():
            return 1
        if ql in title.lower():
            return 2
        if ql in desc.lower():
            return 3
        return 99

    matches = [(t, _score(t)) for t in tasks if _score(t) < 99]
    matches.sort(key=lambda x: (x[1], x[0].get("id", "")))

    _divider("Tickets")
    if not matches:
        print("  (no matches)")
        return shown

    for task, _ in matches[:_CAP]:
        tid = task.get("id", "")
        shown.add(tid)
        title = task.get("title", tid)
        status = task.get("status", "")
        in_logs = " [↑ logs]" if tid in log_ids else ""
        status_tag = f"[{status}]" if status else ""
        print(f"  {tid}{in_logs} {status_tag}: {title[:80]}")

    if len(matches) > _CAP:
        print(f"  … {len(matches) - _CAP} more matches")

    return shown


def _search_code(query: str) -> None:
    """git grep in repo root for literal query."""
    _divider("Code")
    try:
        result = subprocess.run(
            ["git", "grep", "-rn", "-i", "--color=never", query,
             "--", "*.py", "*.md", "*.json"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        lines = [
            l for l in result.stdout.splitlines()
            if not any(skip in l for skip in (".venv/", "test_env/", "__pycache__/", ".jsonl"))
        ]
        if not lines:
            print("  (no hits)")
            return
        for line in lines[:_CAP]:
            print(f"  {line[:140]}")
        if len(lines) > _CAP:
            print(f"  … {len(lines) - _CAP} more hits")
    except subprocess.TimeoutExpired:
        print("  (git grep timed out)")
    except FileNotFoundError:
        print("  (git not found)")


def _palace_where(tokens: list[str], col: str) -> tuple[str, list]:
    """Build (clause, params) ANDing each token against a single column with ILIKE."""
    parts = [f"{col} ILIKE %s" for _ in tokens]
    params = [f"%{t}%" for t in tokens]
    return " AND ".join(parts), params


def _palace_query(cur, table: str, tokens: list[str], cap: int) -> list:
    """Run a token-AND palace query against table (memory_palace or adc.palace)."""
    cols = "path, title, left(content, 160)"
    conds = []
    params = []
    for col in ("path", "title", "content"):
        clause, p = _palace_where(tokens, col)
        conds.append(f"({clause})")
        params.extend(p)
    where = " OR ".join(conds)
    # order: path match wins, then title, then content
    first_tok = f"%{tokens[0]}%"
    order = (
        f"CASE WHEN path ILIKE %s THEN 0 WHEN title ILIKE %s THEN 1 ELSE 2 END"
    )
    cur.execute(
        f"SELECT {cols} FROM {table} WHERE {where} ORDER BY {order} LIMIT %s",
        params + [first_tok, first_tok, cap],
    )
    return cur.fetchall()


def _search_palace(query: str) -> None:
    """Literal token-AND search across Igor memory_palace + adc.palace."""
    _divider("Palace nodes")
    db_url = os.environ.get("UU_HOME_DB_URL") or os.environ.get("UU_HOME_DB_URL")
    if not db_url:
        print("  (UU_HOME_DB_URL / UU_HOME_DB_URL not set — skipping)")
        return
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
    except Exception as e:
        print(f"  (palace unavailable: {e})")
        return

    tokens = query.lower().split()
    hits = 0
    try:
        with conn.cursor() as cur:
            for row in _palace_query(cur, "memory_palace", tokens, _CAP):
                path, title, snippet = row
                print(f"  [{path}] {title or ''}")
                if snippet:
                    print(f"    {snippet[:140].strip()}")
                hits += 1
            try:
                for row in _palace_query(cur, "adc.palace", tokens, _CAP):
                    path, title, snippet = row
                    print(f"  [adc:{path}] {title or ''}")
                    if snippet:
                        print(f"    {snippet[:140].strip()}")
                    hits += 1
            except Exception:
                pass  # adc.palace optional
    except Exception as e:
        print(f"  (palace query failed: {e})")
    finally:
        conn.close()

    if hits == 0:
        print("  (no hits)")


def _search_semantic(query: str, seen_ids: set[str]) -> None:
    """Librarian embedding recall — semantic only, de-duped; suppresses score < 0.4."""
    _divider("Semantic (embedding)")
    try:
        from unseen_university.devices.librarian.recall import recall
        result = recall(query, limit=8)
        if not result.hits:
            print("  (no semantic hits)")
            return
        shown = 0
        suppressed = 0
        for hit in result.hits:
            if hit.score is not None and hit.score < _SEMANTIC_THRESHOLD:
                suppressed += 1
                continue
            if hit.memory_id in seen_ids:
                continue
            score = f"{hit.score:.3f}" if hit.score is not None else "n/a"
            print(f"  [{score}] {hit.memory_id}")
            if hit.narrative:
                print(f"    {hit.narrative[:160]}")
            shown += 1
            if shown >= 3:
                break
        if shown == 0:
            if suppressed > 0:
                print(f"  (all hits below {_SEMANTIC_THRESHOLD} score threshold — no semantic match)")
            else:
                print("  (all semantic hits already shown above)")
    except Exception as e:
        print(f"  (semantic unavailable: {e})")


def main(argv):
    if not argv:
        print("usage: uurecall <query>", file=sys.stderr)
        sys.exit(1)
    query = " ".join(argv)
    print(f"recall: {query!r}")

    log_ids = _search_logs(query)
    shown_ids = _search_tickets(query, log_ids)
    seen_ids = log_ids | shown_ids
    _search_code(query)
    _search_palace(query)
    _search_semantic(query, seen_ids)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])

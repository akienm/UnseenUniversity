#!/usr/bin/env python3
"""Bootstrap inference knowledge graph from historical CC chat logs.

Processes all session transcripts in ~/.claude/projects/ in configurable
N-chunk batches. Logs chunk scope before each chunk fires so the human can
abort between chunks. Re-running is idempotent: already-processed sessions
are skipped via a flat-file tracking list; knowledge nodes use ON CONFLICT
DO UPDATE so re-processing the same patterns updates rather than duplicates.

Usage:
    bootstrap_learning.py                    # process next chunk (prompts between chunks)
    bootstrap_learning.py --chunk-size N    # sessions per chunk (default: 10)
    bootstrap_learning.py --one-chunk       # process one chunk and stop (cron/CI safe)
    bootstrap_learning.py --dry-run         # show what would be processed, no DB writes
    bootstrap_learning.py --status          # show progress summary
    bootstrap_learning.py --db-url URL      # override DB connection
"""

from __future__ import annotations
from unseen_university._uu_root import uu_home

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

_UU_ROOT = Path(os.environ.get("UU_ROOT", str(Path.home() / "dev/src/UnseenUniversity")))
if str(_UU_ROOT) not in sys.path:
    sys.path.insert(0, str(_UU_ROOT))

from devices.librarian.learning_pipeline import LearningPipeline  # noqa: E402

_PROJECTS_DIR = Path(os.environ.get("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")))

_PROCESSED_FILE = Path(
    uu_home()
) / "claudecode" / "bootstrap_processed_sessions.txt"

DEFAULT_DB_URL = (
    os.environ.get("UU_HOME_DB_URL")
    or "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
)
DEFAULT_CHUNK_SIZE = 10


def _classify_message(text: str) -> str:
    """Assign a query_class to a user message based on content heuristics."""
    if not text:
        return "unknown"
    first = text.strip()
    lower = first.lower()[:500]

    if first.startswith("/"):
        cmd = first.split()[0][1:].split("-")[0]  # /sprint-ticket → sprint
        return f"command_{cmd}"

    words = lower.split()
    word_set = set(words)

    ticket_words = {w for w in words if w.startswith("t-") and len(w) > 2}
    if ticket_words or any(w in word_set for w in ("ticket", "sprint", "close", "triage")):
        return "ticket_operation"

    if any(kw in lower for kw in ("error", "fail", "bug", "broken", "crash", "exception", "traceback")):
        return "debugging"

    if any(kw in lower for kw in ("design", "architecture", "approach", "how should we", "what should")):
        return "design_discussion"

    if any(kw in lower for kw in ("how to", "how do", "what is", "what are", "explain", "why does", "why is")):
        return "concept_question"

    if any(kw in lower for kw in ("implement", "add ", "create", "build", "write", "make ")):
        return "implementation_request"

    return "general_chat"


def extract_pairs_from_session(path: Path) -> list[tuple[str, str, str]]:
    """Extract (user_message, assistant_response, session_id) tuples from a transcript."""
    pairs: list[tuple[str, str, str]] = []
    last_user: str | None = None
    session_id = path.stem
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "user":
                content = msg.get("message", {}).get("content", "")
                if isinstance(content, list):
                    text = " ".join(
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                else:
                    text = str(content) if content else ""
                text = text.strip()
                # Skip tool results, system reminders, empty
                if text and not text.startswith("<") and len(text) > 3:
                    last_user = text
            elif mtype == "assistant" and last_user:
                msg_body = msg.get("message", {})
                content = msg_body.get("content", "")
                if isinstance(content, list):
                    text = " ".join(
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                else:
                    text = str(content) if content else ""
                text = text.strip()
                if text and len(text) > 10:
                    pairs.append((last_user, text, session_id))
                    last_user = None
    return pairs


def load_processed() -> set[str]:
    if not _PROCESSED_FILE.exists():
        return set()
    return {line.strip() for line in _PROCESSED_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}


def mark_processed(session_ids: list[str]) -> None:
    _PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _PROCESSED_FILE.open("a", encoding="utf-8") as f:
        for sid in session_ids:
            f.write(sid + "\n")


def discover_sessions(projects_dir: Path) -> list[Path]:
    """Discover all .jsonl session files across all project subdirectories."""
    sessions: list[Path] = []
    for subdir in sorted(projects_dir.iterdir()):
        if subdir.is_dir():
            sessions.extend(sorted(subdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime))
    return sessions


def process_chunk(
    sessions: list[Path],
    pipeline: LearningPipeline,
    dry_run: bool = False,
) -> dict:
    """Process one chunk of sessions. Returns stats dict."""
    all_pairs_by_class: dict[str, list[tuple[str, str]]] = defaultdict(list)
    total_pairs = 0

    for path in sessions:
        try:
            pairs = extract_pairs_from_session(path)
        except Exception:
            continue
        total_pairs += len(pairs)
        for user_msg, assistant_resp, _ in pairs:
            qclass = _classify_message(user_msg)
            all_pairs_by_class[qclass].append((user_msg, assistant_resp))

    nodes_built = 0
    classes_with_data = 0
    if not dry_run:
        for qclass, pairs in all_pairs_by_class.items():
            if len(pairs) >= 3:
                classes_with_data += 1
                requests = [p[0] for p in pairs]
                responses = [p[1] for p in pairs]
                facts = pipeline._extract_facts(qclass, requests, responses)
                if facts:
                    pipeline._store_knowledge_node(qclass, facts)
                    nodes_built += 1
    else:
        classes_with_data = sum(1 for pairs in all_pairs_by_class.values() if len(pairs) >= 3)

    return {
        "sessions_processed": len(sessions),
        "pairs_extracted": total_pairs,
        "query_classes_found": len(all_pairs_by_class),
        "classes_with_3plus": classes_with_data,
        "nodes_built": nodes_built,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
                        help="Sessions per chunk (default: 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be processed without writing to DB")
    parser.add_argument("--status", action="store_true",
                        help="Show processing progress and exit")
    parser.add_argument("--one-chunk", action="store_true",
                        help="Process one chunk then stop (safe for cron/CI)")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL,
                        help="Postgres connection URL")
    args = parser.parse_args()

    all_sessions = discover_sessions(_PROJECTS_DIR)
    processed = load_processed()
    pending = [p for p in all_sessions if p.stem not in processed]

    if args.status:
        print(f"Sessions total:    {len(all_sessions)}")
        print(f"Already processed: {len(processed)}")
        print(f"Pending:           {len(pending)}")
        if pending:
            next_batch = pending[:args.chunk_size]
            print(f"Next chunk:        {len(next_batch)} sessions")
        return

    if not pending:
        print("Bootstrap complete — all sessions already processed.")
        return

    pipeline = LearningPipeline(args.db_url)
    total_chunks = (len(pending) + args.chunk_size - 1) // args.chunk_size
    grand_total: dict[str, int] = {"sessions": 0, "pairs": 0, "nodes": 0}

    for chunk_num, i in enumerate(range(0, len(pending), args.chunk_size), start=1):
        chunk = pending[i : i + args.chunk_size]
        remaining_after = len(pending) - i - len(chunk)

        # Log scope BEFORE chunk fires (cost monitoring)
        print(f"\n--- Chunk {chunk_num}/{total_chunks} ---")
        print(f"  Sessions in chunk: {len(chunk)}")
        print(f"  Remaining after:   {remaining_after}")
        if args.dry_run:
            print("  [DRY RUN — no DB writes]")

        stats = process_chunk(chunk, pipeline, dry_run=args.dry_run)

        print(
            f"  Pairs extracted:   {stats['pairs_extracted']}"
            f"  |  Classes found: {stats['query_classes_found']}"
            f"  |  Classes 3+: {stats['classes_with_3plus']}"
            f"  |  Nodes built: {stats['nodes_built']}"
        )

        if not args.dry_run:
            mark_processed([p.stem for p in chunk])
            processed.update(p.stem for p in chunk)

        grand_total["sessions"] += stats["sessions_processed"]
        grand_total["pairs"] += stats["pairs_extracted"]
        grand_total["nodes"] += stats["nodes_built"]

        if not remaining_after:
            print("\nBootstrap complete — all sessions processed.")
            break

        if args.one_chunk:
            print(f"\n--one-chunk: stopping. Re-run to continue ({remaining_after} sessions remain).")
            break

        # Human abort point between chunks
        try:
            resp = input(f"\nContinue with chunk {chunk_num + 1}/{total_chunks}? [Y/n] ")
            if resp.strip().lower() in ("n", "no"):
                print(f"Aborted. {remaining_after} sessions remain for next run.")
                break
        except EOFError:
            print(f"Non-interactive: stopping after chunk {chunk_num}. Re-run to continue.")
            break

    print(
        f"\nRun total: {grand_total['sessions']} sessions"
        f", {grand_total['pairs']} pairs"
        f", {grand_total['nodes']} nodes"
        + (" [DRY RUN]" if args.dry_run else "")
    )


if __name__ == "__main__":
    main()

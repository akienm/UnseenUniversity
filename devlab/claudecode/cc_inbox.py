"""cc_inbox.py — Claude Code's notification inbox (append-only JSONL).

DEPRECATED: Migrate callers to comms://CC.0 (Router.send) directly.
This shim is dual-write: every append() goes to both the JSONL file and the
IMAP CC.0 mailbox (if unseen_university IMAP is reachable). The JSONL file is
the authoritative read/mark-read source during the migration window; after
verification, the JSONL backend will be removed (Phase 5).

Producers (Igor subsystems) append events; consumer (CC) reads unread, marks
read. The inbox fills the gap where CC only learned about Igor's state when
Akien mentioned it — now Igor's ticket-trip events, pe_chain escalations,
scope_guard HIGH-inertia blocks push here and CC surfaces them on every
user prompt ("you've got mail" pattern) and at session start via context-load.

Storage: ~/.unseen_university/cc_inbox.jsonl — one JSON object per line, append-only.

Entry schema:
    id — monotonic string id (timestamp + counter)
    ts — ISO 8601 UTC
    kind — short category string (ticket_trip, pe_chain_escalate, scope_block, etc.)
    ticket_id — optional ticket id if event is ticket-associated
    summary — one-line summary (displayed inline)
    body — longer body (displayed on request)
    urgency — "low" | "normal" | "high"
    response_expected — bool (is Igor waiting for CC's input?)
    read — bool (flipped by mark_read)

Retention: read_unread purges entries older than INBOX_TTL_DAYS at call time
so the file stays bounded.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

# ── IMAP mirror (dual-write shim) ─────────────────────────────────────────────
# Mirrors every append() to comms://CC.0. Silently no-ops if IMAP is unavailable.
# read_unread() / mark_read() still use the JSONL backend during the migration window.

_imap_router: object = None  # Router | None
_imap_init_done: bool = False


def _get_imap_router():
    """Lazy-init the IMAP mirror. Returns router or None."""
    global _imap_router, _imap_init_done
    if _imap_init_done:
        return _imap_router
    _imap_init_done = True
    try:
        from unseen_university.devices.bus.imap_server import IMAPServer
        from unseen_university.devices.bus.router import Router

        s = IMAPServer()
        s.start()
        s.create_mailbox("CC.0")
        _imap_router = Router(s)
    except Exception as _e:
        print(f"cc_inbox: IMAP mirror unavailable — JSONL authoritative ({_e})", file=sys.stderr)
    return _imap_router


Urgency = Literal["low", "normal", "high"]

INBOX_PATH = Path(
    os.environ.get(
        "CC_INBOX_PATH", str(Path.home() / ".unseen_university" / "cc_inbox.jsonl")
    )
)
INBOX_TTL_DAYS = 30

_WRITE_LOCK = threading.Lock()


@dataclass
class InboxEntry:
    """One CC inbox notification."""

    id: str
    ts: str
    kind: str
    summary: str
    body: str
    urgency: Urgency
    response_expected: bool
    read: bool
    ticket_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "ts": self.ts,
            "kind": self.kind,
            "summary": self.summary,
            "body": self.body,
            "urgency": self.urgency,
            "response_expected": self.response_expected,
            "read": self.read,
        }
        if self.ticket_id is not None:
            d["ticket_id"] = self.ticket_id
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "InboxEntry":
        return cls(
            id=d["id"],
            ts=d["ts"],
            kind=d["kind"],
            summary=d["summary"],
            body=d.get("body", ""),
            urgency=d.get("urgency", "normal"),
            response_expected=d.get("response_expected", False),
            read=d.get("read", False),
            ticket_id=d.get("ticket_id"),
        )


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _make_id() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%S%f")


def append(
    kind: str,
    summary: str,
    body: str = "",
    ticket_id: Optional[str] = None,
    urgency: Urgency = "normal",
    response_expected: bool = False,
    path: Optional[Path] = None,
) -> InboxEntry:
    """Append an entry. Returns the created InboxEntry.

    Producer path; called by Igor subsystems. Thread-safe via _WRITE_LOCK.
    Creates parent directory if missing. Never raises on I/O — logs to stderr
    and returns the entry regardless, so a failing inbox write doesn't break
    the triggering subsystem.

    Scope-tagging (T-test-inbox-tagging): when CC_INBOX_TAG is set in the
    environment, prepend "[<tag>]: " to the summary so tests / debug
    sessions / sandboxes can sweep their own writes via delete_by_prefix
    on exit. Tests set CC_INBOX_TAG=test:<timestamp> in conftest.
    """
    p = path or INBOX_PATH
    tag = os.environ.get("CC_INBOX_TAG", "").strip()
    tagged_summary = f"[{tag}]: {summary}" if tag else summary
    entry = InboxEntry(
        id=_make_id(),
        ts=_now_iso(),
        kind=kind,
        summary=tagged_summary,
        body=body,
        urgency=urgency,
        response_expected=response_expected,
        read=False,
        ticket_id=ticket_id,
    )
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    except Exception as e:
        import sys as _sys

        print(f"cc_inbox.append failed (non-fatal): {e}", file=_sys.stderr)
    # Mirror to IMAP CC.0 (dual-write shim). Silently skipped if unavailable.
    router = _get_imap_router()
    if router is not None:
        try:
            from unseen_university.devices.bus.envelope import Envelope

            env = Envelope(
                from_device="igor",
                to_device="CC.0",
                sent_at=entry.ts,
                payload=entry.to_dict(),
            )
            router.send("comms://CC.0", env)
        except Exception as _e:
            print(f"cc_inbox: IMAP mirror send failed ({_e})", file=sys.stderr)
    return entry


def _load_all(path: Optional[Path] = None) -> list[InboxEntry]:
    p = path or INBOX_PATH
    if not p.exists():
        return []
    entries: list[InboxEntry] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(InboxEntry.from_dict(json.loads(line)))
            except Exception:
                continue
    return entries


def _rewrite_all(entries: list[InboxEntry], path: Optional[Path] = None) -> None:
    p = path or INBOX_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp, p)


def read_unread(
    path: Optional[Path] = None,
    purge_ttl_days: int = INBOX_TTL_DAYS,
) -> list[InboxEntry]:
    """Return unread entries newest-first. TTL-purges old entries as a side effect.

    Consumer path; CC calls on every user prompt + at context-load. The
    purge keeps the file bounded — entries older than purge_ttl_days are
    dropped on read (so we don't carry stale inbox forever).
    """
    entries = _load_all(path)
    if not entries:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=purge_ttl_days)

    def _is_fresh(e: InboxEntry) -> bool:
        try:
            ts = datetime.fromisoformat(e.ts.replace("Z", "+00:00"))
            return ts >= cutoff
        except Exception:
            return True

    fresh = [e for e in entries if _is_fresh(e)]
    if len(fresh) != len(entries):
        _rewrite_all(fresh, path)
    unread = [e for e in fresh if not e.read]
    # Sort by id (monotonic, microsecond resolution) rather than ts
    # (second resolution) — entries appended within the same second
    # still order correctly.
    unread.sort(key=lambda e: e.id, reverse=True)
    return unread


def mark_read(entry_id: str, path: Optional[Path] = None) -> bool:
    """Flip read=True for the entry with matching id. Returns True if found."""
    entries = _load_all(path)
    changed = False
    for e in entries:
        if e.id == entry_id:
            if not e.read:
                e.read = True
                changed = True
            break
    if changed:
        _rewrite_all(entries, path)
    return changed


def mark_all_read(path: Optional[Path] = None) -> int:
    """Flip read=True for every unread entry. Returns count changed."""
    entries = _load_all(path)
    count = 0
    for e in entries:
        if not e.read:
            e.read = True
            count += 1
    if count > 0:
        _rewrite_all(entries, path)
    return count


def delete_by_prefix(prefix: str, path: Optional[Path] = None) -> int:
    """Delete entries whose summary starts with `prefix`. Returns count removed.

    Sweep helper for scope-tagged entries (T-test-inbox-tagging). pytest
    conftest invokes this with prefix=f"[{CC_INBOX_TAG}]" at session_finish
    so test runs don't leave residue in the production inbox. Generalizes
    to any scope (debug:, sandbox:, dev:) — set CC_INBOX_TAG before
    appending, sweep with the matching prefix on exit.

    No-op + returns 0 when the inbox file doesn't exist.
    """
    if not prefix:
        return 0
    p = path or INBOX_PATH
    if not p.exists():
        return 0
    entries = _load_all(p)
    kept = [e for e in entries if not e.summary.startswith(prefix)]
    removed = len(entries) - len(kept)
    if removed > 0:
        _rewrite_all(kept, p)
    return removed


def _cli(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="CC inbox CLI.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list unread entries")
    p_list.add_argument("--all", action="store_true", help="include read entries")

    p_append = sub.add_parser("append", help="append an entry (for testing)")
    p_append.add_argument("--kind", required=True)
    p_append.add_argument("--summary", required=True)
    p_append.add_argument("--body", default="")
    p_append.add_argument("--ticket-id", default=None)
    p_append.add_argument(
        "--urgency", default="normal", choices=["low", "normal", "high"]
    )

    p_mark = sub.add_parser("mark-read", help="mark an entry read by id")
    p_mark.add_argument("entry_id")

    p_mark_all = sub.add_parser("mark-all-read", help="mark every unread entry read")

    args = ap.parse_args(argv)

    if args.cmd == "list":
        if args.all:
            entries = _load_all()
        else:
            entries = read_unread()
        if not entries:
            print("inbox empty" if not args.all else "no entries")
            return 0
        for e in entries:
            urg = {"low": "·", "normal": " ", "high": "!"}.get(e.urgency, " ")
            rd = "r" if e.read else "u"
            tk = f" [{e.ticket_id}]" if e.ticket_id else ""
            print(f"[{rd}]{urg} {e.ts} {e.kind}{tk}: {e.summary}")
        return 0

    if args.cmd == "append":
        entry = append(
            kind=args.kind,
            summary=args.summary,
            body=args.body,
            ticket_id=args.ticket_id,
            urgency=args.urgency,
        )
        print(f"appended: {entry.id}")
        return 0

    if args.cmd == "mark-read":
        ok = mark_read(args.entry_id)
        print("marked" if ok else "not found")
        return 0 if ok else 1

    if args.cmd == "mark-all-read":
        n = mark_all_read()
        print(f"marked {n} entry(ies) as read")
        return 0

    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_cli(sys.argv[1:]))

"""
YGM nudge check — called by the Claude Code UserPromptSubmit hook.

Reads the Claude mailbox (CC.0 or CC.<session>) for unread messages.
If unread messages exist, outputs a nudge header to stdout for CC to
prepend to the current query context.

v1 format (D-adc-phase-3-2026-04-27):
  --- INBOX (N unread, from: sender1, sender2) ---

Full message bodies stay in the mailbox — Claude reads them explicitly
via rack MCP tools. This token is the signal, not the content.

Exit codes:
  0 — check complete (with or without output)
  1 — IMAP connection error (logged to stderr, never crashes CC)

Usage (called by CC hook):
  python3 -m devices.claude.ygm_check
  # or directly:
  python3 /path/to/ygm_check.py
"""

from __future__ import annotations

import os
import sys
from unseen_university.identity import instance_id


def _get_imap_server():
    """Connect to the running IMAP server (test stub or Dovecot)."""
    test_mode = os.environ.get("AGENT_DATACENTER_TEST_MODE", "0") == "1"
    if test_mode:
        return None  # In test mode, no live IMAP to query

    try:
        import imaplib

        host = os.environ.get("IMAP_HOST", "127.0.0.1")
        port = int(os.environ.get("IMAP_PORT", "10143"))
        user = os.environ.get("IMAP_USER", "rack")
        password = os.environ.get("IMAP_PASSWORD", "rack")

        conn = imaplib.IMAP4(host, port)
        conn.login(user, password)
        return conn
    except Exception as exc:
        print(f"[ygm] IMAP connect failed: {exc}", file=sys.stderr)
        return None


def _check_mailbox_imap(conn, mailbox: str) -> list[str]:
    """Return list of from_device values for unseen messages with intent for mailbox."""
    import json

    from unseen_university.announce.notify import has_intent

    try:
        status, _ = conn.select(mailbox, readonly=True)
        if status != "OK":
            return []
        _, data = conn.search(None, "UNSEEN")
        if not data or not data[0]:
            return []
        msg_nums = data[0].split()
        senders = []
        for num in msg_nums:
            _, msg_data = conn.fetch(num, "(BODY[])")
            if msg_data and msg_data[0]:
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                try:
                    envelope = json.loads(raw)
                    if not has_intent(envelope, mailbox):
                        continue
                    sender = envelope.get("from_device", "unknown")
                    if sender not in senders:
                        senders.append(sender)
                except (json.JSONDecodeError, AttributeError):
                    senders.append("unknown")
        return senders
    except Exception:
        return []


def _check_jsonl_fallback(mailbox: str) -> list[str]:
    """
    Fallback: read unread from TheIgors cc_inbox.jsonl if IMAP unavailable.
    Used when the IMAP bus isn't running but the old path is still active.
    """
    try:
        import json

        cc_inbox_path = os.path.expanduser(
            f"~/.unseen_university/{instance_id()}/cc_inbox.jsonl"
        )
        if not os.path.exists(cc_inbox_path):
            return []
        senders = []
        with open(cc_inbox_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if not entry.get("read", False):
                        sender = entry.get("from_device", "igor")
                        if sender not in senders:
                            senders.append(sender)
                except json.JSONDecodeError:
                    continue
        return senders
    except Exception:
        return []


def run(mailbox: str | None = None) -> str | None:
    """
    Check for unread messages and return the nudge string, or None.

    Returns the nudge header string if there are unread messages,
    None if the mailbox is empty or IMAP is unavailable.
    """
    from devices.claude.constants import get_session_mailbox

    target = mailbox or get_session_mailbox()

    conn = _get_imap_server()
    if conn is not None:
        senders = _check_mailbox_imap(conn, target)
        # Also check CC.0 global if we're on a session mailbox
        from devices.claude.constants import GLOBAL_MAILBOX

        if target != GLOBAL_MAILBOX:
            senders += [
                s for s in _check_mailbox_imap(conn, GLOBAL_MAILBOX) if s not in senders
            ]
        try:
            conn.logout()
        except Exception:
            pass
    else:
        # Fallback to JSONL inbox (dual-write window)
        senders = _check_jsonl_fallback(target)

    if not senders:
        return None

    sender_list = ", ".join(senders)
    n = len(senders) if len(senders) == 1 else f"{len(senders)}+"
    return f"--- INBOX ({n} unread, from: {sender_list}) ---"


def main() -> None:
    """Entry point for CC UserPromptSubmit hook."""
    nudge = run()
    if nudge:
        print(nudge)
    sys.exit(0)


if __name__ == "__main__":
    main()

"""
Unified network listener.

Single background thread that polls all configured network sources and
normalizes everything into one incoming queue for Igor's main loop.

Current sources:
  - Discord  (drains discord_bot.incoming every 0.5s - fast, websocket-backed)
  - Gmail    (polls IMAP for UNSEEN every GMAIL_POLL_INTERVAL seconds)
  - Web      (drains web server.incoming — browser chat messages)

Adding a new source:
  1. Write a _poll_<source>() function that puts NetworkMessage objects into `incoming`
  2. Call it inside _run_loop()
  3. Add any required env vars to .env.example

GTalk/Google Talk was shut down in 2022. Its replacement is Google Chat,
which requires a Google Workspace account and OAuth2. Add _poll_google_chat()
here when ready.
"""

import imaplib
import email as email_lib
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from email.header import decode_header as _imap_decode
from typing import Any

from . import discord_bot
from ..web import server as web_server

# ── Unified queue ─────────────────────────────────────────────────────────────
incoming: queue.Queue = queue.Queue()   # All sources → Igor

# ── Config ────────────────────────────────────────────────────────────────────
DISCORD_POLL_INTERVAL  = 0.5    # seconds - fast, websocket does the heavy lifting
GMAIL_POLL_INTERVAL    = 300    # seconds - IMAP polling is slow, don't hammer it
GMAIL_MAX_PER_POLL     = 10     # max new emails to process per poll cycle

_listener_thread: threading.Thread | None = None


# ── Message model ─────────────────────────────────────────────────────────────

@dataclass
class NetworkMessage:
    source: str               # "discord", "gmail", "google_chat", …
    content: str              # The text body
    author: str               # Display name or email
    reply_info: dict = field(default_factory=dict)  # Source-specific reply metadata
    raw: Any = None           # Original source object (for advanced use)
    received_at: float = 0.0  # time.monotonic() at moment of queue insertion (#139)


# ── Source pollers ─────────────────────────────────────────────────────────────

def _poll_discord():
    """Drain discord_bot.incoming into unified queue. Non-blocking."""
    while True:
        try:
            msg = discord_bot.incoming.get_nowait()
            incoming.put(NetworkMessage(
                source="discord",
                content=msg.content,
                author=msg.author,
                reply_info={
                    "channel_id":   msg.channel_id,
                    "channel_name": msg.channel_name,
                    "guild_name":   msg.guild_name,
                    "message_id":   msg.message_id,
                },
                raw=msg,
                received_at=time.monotonic(),
            ))
        except queue.Empty:
            break


def _poll_web():
    """Drain web_server.incoming into unified queue. Non-blocking."""
    while True:
        try:
            msg = web_server.incoming.get_nowait()
            incoming.put(NetworkMessage(
                source="web",
                content=msg["content"],
                author=msg.get("author", "web-user"),
                reply_info={"client_id": msg.get("client_id")},
                received_at=time.monotonic(),
            ))
        except queue.Empty:
            break


def _decode_header_str(value: str) -> str:
    parts = _imap_decode(value or "")
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result)


def _poll_gmail():
    """Poll Gmail IMAP for UNSEEN messages. Puts NetworkMessages into incoming."""
    user = os.getenv("GMAIL_USER", "")
    pw   = os.getenv("GMAIL_APP_PASSWORD", "")
    if not user or not pw:
        return

    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993) as mail:
            mail.login(user, pw)
            mail.select("INBOX")

            _, data = mail.search(None, "UNSEEN")
            ids = data[0].split()
            if not ids:
                return

            for msg_id in ids[-GMAIL_MAX_PER_POLL:]:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                msg = email_lib.message_from_bytes(msg_data[0][1])

                subject   = _decode_header_str(msg.get("Subject", "(no subject)"))
                sender    = _decode_header_str(msg.get("From", "(unknown)"))
                reply_to  = _decode_header_str(msg.get("Reply-To", "") or msg.get("From", ""))

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                            break
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

                incoming.put(NetworkMessage(
                    source="gmail",
                    content=body.strip()[:2000],
                    author=sender,
                    reply_info={
                        "from":     sender,
                        "subject":  subject,
                        "reply_to": reply_to,
                    },
                    received_at=time.monotonic(),
                ))

    except Exception:
        pass  # Never block Igor on Gmail failure; it'll retry next cycle


# ── Listener thread ────────────────────────────────────────────────────────────

def _run_loop():
    last_gmail = 0.0

    while True:
        _poll_discord()
        _poll_web()

        now = time.monotonic()
        if now - last_gmail >= GMAIL_POLL_INTERVAL:
            _poll_gmail()
            last_gmail = now

        # Add more pollers here:
        # _poll_google_chat()
        # _poll_slack()
        # _poll_matrix()

        time.sleep(DISCORD_POLL_INTERVAL)


def start():
    """Start the unified network listener daemon thread. Non-blocking."""
    global _listener_thread
    if _listener_thread and _listener_thread.is_alive():
        return
    _listener_thread = threading.Thread(
        target=_run_loop, daemon=True, name="network-listener"
    )
    _listener_thread.start()


def is_running() -> bool:
    return _listener_thread is not None and _listener_thread.is_alive()

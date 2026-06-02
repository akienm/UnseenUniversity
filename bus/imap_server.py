"""
IMAPServer — IMAP bus backend.

Production: connects to a running Dovecot instance.
Tests:      spins up an in-process asyncio stub (no system dependencies).

Mode is selected by AGENT_DATACENTER_TEST_MODE=1. The stub raises
RuntimeError if called without that flag — prevents silent test-only
code running in production.

Usage:
    server = IMAPServer()
    server.start()
    server.create_mailbox("CC.0")
    server.append("CC.0", envelope)
    msgs = server.fetch_unseen("CC.0")  # marks SEEN
    count = server.unseen_count("CC.0")
    server.stop()
"""

from __future__ import annotations

import asyncio
import imaplib
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from bus.envelope import Envelope

log = logging.getLogger(__name__)

_TEST_MODE = os.environ.get("AGENT_DATACENTER_TEST_MODE", "") == "1"

# ── In-process stub (test mode only) ──────────────────────────────────────────

_STUB_MAILBOXES: dict[str, list[bytes]] = defaultdict(list)
_STUB_SEEN: dict[str, set[int]] = defaultdict(set)
# threading.Event so set() is safe from any thread (not just the event loop)
_STUB_IDLE_EVENTS: dict[str, list[threading.Event]] = defaultdict(list)
_CRLF = b"\r\n"


async def _stub_handle_client(reader, writer):
    writer.write(b"* OK IMAP stub ready" + _CRLF)
    await writer.drain()
    mailbox = None
    idling = False
    tag = ""

    while True:
        line = await reader.readline()
        if not line:
            break
        line = line.rstrip(b"\r\n").decode(errors="replace")
        parts = line.split(None, 2)
        if not parts:
            continue
        tag = parts[0]
        cmd = parts[1].upper() if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""

        if idling and cmd != "DONE":
            continue

        def send(s: str) -> None:
            writer.write(s.encode())

        if cmd == "CAPABILITY":
            send(f"* CAPABILITY IMAP4rev1 IDLE\r\n{tag} OK CAPABILITY done\r\n")
        elif cmd in ("LOGIN", "AUTHENTICATE"):
            send(f"{tag} OK logged in\r\n")
        elif cmd == "SELECT":
            mailbox = rest.strip().strip('"')
            n = len(_STUB_MAILBOXES[mailbox])
            send(f"* {n} EXISTS\r\n{tag} OK [READ-WRITE] SELECT done\r\n")
        elif cmd == "LIST":
            for mbox in _STUB_MAILBOXES:
                send(f'* LIST (\\HasNoChildren) "/" "{mbox}"\r\n')
            send(f"{tag} OK LIST done\r\n")
        elif cmd == "STATUS":
            m = re.match(r'"?([^"]+)"?\s+\(UNSEEN\)', rest)
            if m:
                mbox = m.group(1)
                unseen = sum(
                    1
                    for i, _ in enumerate(_STUB_MAILBOXES[mbox])
                    if i not in _STUB_SEEN[mbox]
                )
                send(f'* STATUS "{mbox}" (UNSEEN {unseen})\r\n{tag} OK STATUS done\r\n')
            else:
                send(f"{tag} OK STATUS done\r\n")
        elif cmd == "APPEND":
            m = re.match(r'"?([^"]+)"?\s*(?:\(\S+\))?\s*\{(\d+)\}', rest)
            if m:
                mbox, size = m.group(1), int(m.group(2))
                send("+ Ready\r\n")
                await writer.drain()
                body = await reader.read(size + 2)
                _STUB_MAILBOXES[mbox].append(body)
                for ev in _STUB_IDLE_EVENTS[mbox]:
                    ev.set()
                send(f"{tag} OK APPEND done\r\n")
            else:
                send(f"{tag} BAD APPEND syntax\r\n")
        elif cmd == "SEARCH":
            mbox_key = mailbox or "INBOX"
            if "UNSEEN" in rest.upper():
                unseen = [
                    str(i + 1)
                    for i in range(len(_STUB_MAILBOXES[mbox_key]))
                    if i not in _STUB_SEEN[mbox_key]
                ]
                send(f"* SEARCH {' '.join(unseen)}\r\n{tag} OK SEARCH done\r\n")
            else:
                all_ids = [str(i + 1) for i in range(len(_STUB_MAILBOXES[mbox_key]))]
                send(f"* SEARCH {' '.join(all_ids)}\r\n{tag} OK SEARCH done\r\n")
        elif cmd == "FETCH":
            mbox_key = mailbox or "INBOX"
            m = re.match(r"(\d+)(?::(\d+|\*))?\s+(.*)", rest)
            if m:
                start = int(m.group(1)) - 1
                msgs = _STUB_MAILBOXES[mbox_key]
                body = msgs[start] if 0 <= start < len(msgs) else b""
                send(f"* {start + 1} FETCH (RFC822 {{{len(body)}}})\r\n")
                await writer.drain()
                writer.write(body)
                send(f"\r\n{tag} OK FETCH done\r\n")
        elif cmd == "STORE":
            mbox_key = mailbox or "INBOX"
            m = re.match(
                r"(\d+)(?::(\d+|\*))?\s+\+FLAGS\s+\(\\Seen\)", rest, re.IGNORECASE
            )
            if m:
                idx = int(m.group(1)) - 1
                _STUB_SEEN[mbox_key].add(idx)
            send(f"{tag} OK STORE done\r\n")
        elif cmd == "IDLE":
            send("+ idling\r\n")
            await writer.drain()
            idling = True
            ev = threading.Event()
            mbox_key = mailbox or "INBOX"
            _STUB_IDLE_EVENTS[mbox_key].append(ev)
            # run_in_executor so ev.wait() doesn't block the event loop.
            # Poll with a short timeout so a closed writer (peer disconnect)
            # exits IDLE cleanly — without this, the executor hangs on the
            # threading.Event forever and the asyncio loop can't shutdown.
            loop = asyncio.get_event_loop()
            woke = False
            while not ev.is_set():
                await loop.run_in_executor(None, ev.wait, 0.05)
                if writer.is_closing():
                    break
            if ev.is_set():
                woke = True
            _STUB_IDLE_EVENTS[mbox_key].remove(ev)
            if woke:
                n = len(_STUB_MAILBOXES[mbox_key])
                send(f"* {n} EXISTS\r\n")
            else:
                # Peer disconnected mid-IDLE — exit handler.
                break
        elif cmd == "DONE":
            idling = False
            send(f"{tag} OK IDLE terminated\r\n")
        elif cmd == "LOGOUT":
            send(f"* BYE\r\n{tag} OK LOGOUT done\r\n")
            break
        else:
            send(f"{tag} OK (stub — {cmd} ignored)\r\n")
        await writer.drain()
    writer.close()


class _StubServer:
    """Asyncio IMAP stub — test mode only."""

    def __init__(self, host: str = "127.0.0.1", port: int = 10143) -> None:
        self.host = host
        self.port = port
        self._server = None
        self._thread = None
        self._loop = None

    def start(self) -> None:
        _STUB_MAILBOXES.clear()
        _STUB_SEEN.clear()
        _STUB_IDLE_EVENTS.clear()
        ready = threading.Event()

        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _serve():
                # Use port 0 so the OS assigns a free port — avoids collisions
                # with the live IMAP server when running tests alongside production.
                self._server = await asyncio.start_server(
                    _stub_handle_client, self.host, 0
                )
                self.port = self._server.sockets[0].getsockname()[1]
                ready.set()
                async with self._server:
                    await self._server.serve_forever()

            self._loop.run_until_complete(_serve())

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        ready.wait(timeout=5)
        log.info("IMAP stub started on %s:%s", self.host, self.port)

    def stop(self) -> None:
        if self._server and self._loop:
            self._loop.call_soon_threadsafe(self._server.close)


# ── Production client (wraps imaplib against Dovecot) ─────────────────────────


class _DovecotClient:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    def _connect(self) -> imaplib.IMAP4:
        return imaplib.IMAP4(self.host, self.port)

    def create_mailbox(self, name: str) -> None:
        M = self._connect()
        M.create(name)
        M.logout()

    def delete_mailbox(self, name: str) -> None:
        M = self._connect()
        M.delete(name)
        M.logout()

    def list_mailboxes(self) -> list[str]:
        M = self._connect()
        _, data = M.list()
        M.logout()
        result = []
        for item in data:
            if item:
                parts = item.decode().split('"')
                result.append(parts[-1].strip())
        return result

    def append(self, mailbox: str, raw: bytes) -> None:
        M = self._connect()
        M.append(mailbox, None, None, raw)
        M.logout()

    def unseen_count(self, mailbox: str) -> int:
        M = self._connect()
        M.select(mailbox)
        _, data = M.search(None, "UNSEEN")
        M.logout()
        ids = data[0].split() if data[0] else []
        return len(ids)

    def fetch_unseen(self, mailbox: str) -> list[bytes]:
        M = self._connect()
        M.select(mailbox)
        _, data = M.search(None, "UNSEEN")
        ids = data[0].split() if data[0] else []
        messages = []
        for seq in ids:
            _, msg_data = M.fetch(seq, "(RFC822)")
            if msg_data and msg_data[0]:
                messages.append(msg_data[0][1])
            M.store(seq, "+FLAGS", r"\Seen")
        M.logout()
        return messages

    def fetch_recent(self, mailbox: str, limit: int = 20) -> list[bytes]:
        """Return last `limit` messages without marking SEEN."""
        M = self._connect()
        try:
            M.select(mailbox, readonly=True)
            _, data = M.search(None, "ALL")
            ids = data[0].split() if data[0] else []
            tail = ids[-limit:] if len(ids) > limit else ids
            messages = []
            for seq in tail:
                _, msg_data = M.fetch(seq, "(RFC822)")
                if msg_data and msg_data[0]:
                    messages.append(msg_data[0][1])
            return messages
        finally:
            M.logout()


# ── Public IMAPServer facade ───────────────────────────────────────────────────


class IMAPServer:
    """
    Unified IMAP bus interface. Selects Dovecot or stub based on AGENT_DATACENTER_TEST_MODE.
    """

    SHARED_MAILBOX = "Shared"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 10143 if _TEST_MODE else 143,
    ) -> None:
        self.host = host
        self.port = port
        self._stub: _StubServer | None = None
        self._client: _DovecotClient | None = None

    def start(self) -> None:
        if _TEST_MODE:
            self._stub = _StubServer(self.host, self.port)
            self._stub.start()
            self.port = self._stub.port  # actual port after OS assignment
        # In production Dovecot is a system service — we just connect to it.
        self._client = _DovecotClient(self.host, self.port)
        # Ensure shared mailbox exists
        try:
            self.create_mailbox(self.SHARED_MAILBOX)
        except Exception:
            pass  # already exists

    def stop(self) -> None:
        if self._stub:
            self._stub.stop()

    def create_mailbox(self, name: str) -> None:
        if _TEST_MODE:
            _STUB_MAILBOXES.setdefault(name, [])
        else:
            assert self._client
            self._client.create_mailbox(name)

    def delete_mailbox(self, name: str) -> None:
        """Soft-delete: log retained per 24hr retention policy."""
        if _TEST_MODE:
            _STUB_MAILBOXES.pop(name, None)
        else:
            assert self._client
            self._client.delete_mailbox(name)

    def list_mailboxes(self) -> list[str]:
        if _TEST_MODE:
            return list(_STUB_MAILBOXES.keys())
        assert self._client
        return self._client.list_mailboxes()

    def append(self, mailbox: str, envelope: Envelope) -> None:
        raw = envelope.to_json().encode()
        if _TEST_MODE:
            _STUB_MAILBOXES[mailbox].append(raw)
            for ev in _STUB_IDLE_EVENTS[mailbox]:
                ev.set()
        else:
            assert self._client
            self._client.append(mailbox, raw)

    def unseen_count(self, mailbox: str) -> int:
        if _TEST_MODE:
            msgs = _STUB_MAILBOXES[mailbox]
            seen = _STUB_SEEN[mailbox]
            return sum(1 for i in range(len(msgs)) if i not in seen)
        assert self._client
        return self._client.unseen_count(mailbox)

    def fetch_unseen(self, mailbox: str) -> list[Envelope]:
        """Fetch unseen messages and mark them SEEN."""
        if _TEST_MODE:
            msgs = _STUB_MAILBOXES[mailbox]
            seen = _STUB_SEEN[mailbox]
            result = []
            for i, raw in enumerate(msgs):
                if i not in seen:
                    result.append(Envelope.from_json(raw.decode()))
                    seen.add(i)
            return result
        assert self._client
        raw_msgs = self._client.fetch_unseen(mailbox)
        return [Envelope.from_json(r.decode()) for r in raw_msgs]

    def fetch_recent(self, mailbox: str, limit: int = 20) -> list[Envelope]:
        """Return the last `limit` messages without marking them SEEN."""
        if _TEST_MODE:
            msgs = list(_STUB_MAILBOXES.get(mailbox, []))
            tail = msgs[-limit:] if len(msgs) > limit else msgs
            return [Envelope.from_json(raw.decode()) for raw in tail]
        assert self._client
        raw_msgs = self._client.fetch_recent(mailbox, limit)
        return [Envelope.from_json(r.decode()) for r in raw_msgs]

    def purge_old_messages(self, retention_hours: int = 24) -> int:
        """
        Remove messages older than retention_hours from all mailboxes (test mode only).

        Expiry is based on Envelope.sent_at. Both SEEN and UNSEEN messages are retained
        until the cutoff — reading a message does NOT trigger early deletion.
        Returns total number of messages purged across all mailboxes.

        In production, Dovecot handles expiry via the expire plugin (see
        config/dovecot.conf.template). This method is a no-op outside test mode.
        """
        if not _TEST_MODE:
            return 0

        import json as _json

        cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
        total_purged = 0

        for mbox in list(_STUB_MAILBOXES.keys()):
            msgs = _STUB_MAILBOXES[mbox]
            seen = _STUB_SEEN[mbox]

            kept_msgs: list[bytes] = []
            kept_old_indices: set[int] = set()  # old indices that are being kept
            for old_idx, raw in enumerate(msgs):
                try:
                    data = _json.loads(raw.decode())
                    sent_at = datetime.fromisoformat(data.get("sent_at", ""))
                    if sent_at.tzinfo is None:
                        sent_at = sent_at.replace(tzinfo=timezone.utc)
                    expired = sent_at < cutoff
                except Exception:
                    expired = False  # malformed envelope — keep it

                if not expired:
                    kept_old_indices.add(old_idx)
                    kept_msgs.append(raw)

            purged = len(msgs) - len(kept_msgs)
            if purged:
                # Rebuild seen set with updated indices
                new_seen: set[int] = set()
                new_idx = 0
                for old_idx in range(len(msgs)):
                    if old_idx in kept_old_indices:
                        if old_idx in seen:
                            new_seen.add(new_idx)
                        new_idx += 1

                _STUB_MAILBOXES[mbox] = kept_msgs
                _STUB_SEEN[mbox] = new_seen
                total_purged += purged
                log.info("purged %d expired message(s) from mailbox %r", purged, mbox)

        return total_purged

    def idle_wait(self, mailbox: str, timeout_s: float = 25 * 60) -> bool:
        """Block until a message arrives in mailbox or timeout_s elapses.

        Returns True if a message arrived, False if the timeout expired.

        In test mode (stub): registers a threading.Event in _STUB_IDLE_EVENTS so
        any append() wakes this call immediately.

        In production (Dovecot): falls back to polling every 2s for now.
        True IDLE over imaplib requires raw socket gymnastics; that upgrade
        is a follow-on task once the Librarian is running in production.
        """
        if _TEST_MODE:
            ev = threading.Event()
            _STUB_IDLE_EVENTS[mailbox].append(ev)
            try:
                woke = ev.wait(timeout=timeout_s)
            finally:
                try:
                    _STUB_IDLE_EVENTS[mailbox].remove(ev)
                except ValueError:
                    pass
            return woke

        # Production fallback: poll until a message appears or timeout
        deadline = time.monotonic() + timeout_s
        poll_interval = 2.0
        assert self._client
        baseline = self._client.unseen_count(mailbox)
        while time.monotonic() < deadline:
            try:
                if self._client.unseen_count(mailbox) > baseline:
                    return True
            except Exception as exc:
                log.debug("idle_wait poll error (non-fatal): %s", exc)
            remaining = deadline - time.monotonic()
            time.sleep(min(poll_interval, max(0, remaining)))
        return False

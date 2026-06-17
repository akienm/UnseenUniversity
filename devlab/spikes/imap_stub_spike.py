"""
Spike: minimal in-process IMAP server for test fixtures.
Handles: CAPABILITY, LOGIN, SELECT, APPEND, IDLE, FETCH, LOGOUT.
No TLS, no auth checking — for test use only.
"""
import asyncio, re, textwrap
from collections import defaultdict

CRLF = b"\r\n"
MAILBOXES = defaultdict(list)  # {name: [message_bytes, ...]}
IDLE_WAITERS = defaultdict(list)  # {name: [asyncio.Event, ...]}

async def handle_client(reader, writer):
    writer.write(b"* OK IMAP stub ready" + CRLF)
    await writer.drain()
    mailbox = None
    idling = False

    while True:
        line = await reader.readline()
        if not line:
            break
        line = line.rstrip(b"\r\n").decode(errors="replace")
        parts = line.split(None, 2)
        if not parts:
            continue
        tag, cmd = parts[0], parts[1].upper() if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""

        if idling and cmd != "DONE":
            continue
        if cmd == "CAPABILITY":
            writer.write(f"* CAPABILITY IMAP4rev1 IDLE{chr(13)}{chr(10)}".encode())
            writer.write(f"{tag} OK CAPABILITY done{chr(13)}{chr(10)}".encode())
        elif cmd in ("LOGIN", "AUTHENTICATE"):
            writer.write(f"{tag} OK logged in{chr(13)}{chr(10)}".encode())
        elif cmd == "SELECT":
            mailbox = rest.strip().strip('"')
            n = len(MAILBOXES[mailbox])
            writer.write(f"* {n} EXISTS{chr(13)}{chr(10)}".encode())
            writer.write(f"{tag} OK [READ-WRITE] SELECT done{chr(13)}{chr(10)}".encode())
        elif cmd == "APPEND":
            m = re.match(r'(\S+)\s*(?:\(\S+\))?\s*\{(\d+)\}', rest)
            if m:
                mbox, size = m.group(1).strip('"'), int(m.group(2))
                writer.write(b"+ Ready" + CRLF)
                await writer.drain()
                body = await reader.read(size + 2)
                MAILBOXES[mbox].append(body)
                for ev in IDLE_WAITERS[mbox]:
                    ev.set()
                writer.write(f"{tag} OK APPEND done{chr(13)}{chr(10)}".encode())
        elif cmd == "IDLE":
            writer.write(b"+ idling" + CRLF)
            await writer.drain()
            idling = True
            ev = asyncio.Event()
            IDLE_WAITERS[mailbox or "INBOX"].append(ev)
            await ev.wait()
            IDLE_WAITERS[mailbox or "INBOX"].remove(ev)
            writer.write(f"* {len(MAILBOXES[mailbox or 'INBOX'])} EXISTS{chr(13)}{chr(10)}".encode())
        elif cmd == "DONE":
            idling = False
            writer.write(f"{tag} OK IDLE terminated{chr(13)}{chr(10)}".encode())
        elif cmd == "LOGOUT":
            writer.write(f"* BYE{chr(13)}{chr(10)}{tag} OK LOGOUT done{chr(13)}{chr(10)}".encode())
            break
        else:
            writer.write(f"{tag} OK (stub — {cmd} ignored){chr(13)}{chr(10)}".encode())
        await writer.drain()
    writer.close()

async def main():
    srv = await asyncio.start_server(handle_client, "127.0.0.1", 10143)
    print("IMAP stub on port 10143")
    async with srv:
        await srv.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())

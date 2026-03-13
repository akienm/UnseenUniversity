"""
Gmail tools - send and read email via SMTP/IMAP.
Uses app password authentication (no OAuth needed).
"""

import imaplib
import os
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header

from .registry import Tool, registry

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def _credentials() -> tuple[str, str]:
    user = os.getenv("GMAIL_USER", "")
    pw = os.getenv("GMAIL_APP_PASSWORD", "")
    if not user or not pw:
        raise ValueError("GMAIL_USER and GMAIL_APP_PASSWORD must be set in .env")
    return user, pw


def _decode_header_value(value: str) -> str:
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email from Igor's Gmail account."""
    import os as _os
    if not _os.getenv("IGOR_EMAIL_SEND_ENABLED", "false").lower() in ("1", "true", "yes"):
        return (
            "Email sending is currently gated. "
            "Set IGOR_EMAIL_SEND_ENABLED=true in .env to enable outbound email."
        )
    try:
        user, pw = _credentials()

        msg = MIMEMultipart()
        msg["From"] = user
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(user, pw)
            server.send_message(msg)

        return f"Sent: '{subject}' → {to}"
    except Exception as e:
        return f"Error sending email: {e}"


def read_inbox(count: int = 10) -> str:
    """Read the most recent emails from Igor's inbox."""
    try:
        user, pw = _credentials()

        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as mail:
            mail.login(user, pw)
            mail.select("INBOX")

            _, data = mail.search(None, "ALL")
            ids = data[0].split()
            recent = ids[-count:] if len(ids) >= count else ids
            recent = list(reversed(recent))  # Newest first

            results = []
            for msg_id in recent:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])

                subject = _decode_header_value(msg.get("Subject", "(no subject)"))
                sender = _decode_header_value(msg.get("From", "(unknown)"))
                date = msg.get("Date", "")

                # Get plain text body
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                            break
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

                body_preview = body.strip()[:200].replace("\n", " ")
                results.append(f"From: {sender}\nDate: {date}\nSubject: {subject}\n{body_preview}")

            return f"Inbox ({len(results)} messages):\n\n" + "\n\n---\n\n".join(results)

    except Exception as e:
        return f"Error reading inbox: {e}"


def search_email(query: str, count: int = 5) -> str:
    """Search Igor's Gmail inbox."""
    try:
        user, pw = _credentials()

        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as mail:
            mail.login(user, pw)
            mail.select("INBOX")

            # IMAP search - subject or body
            _, data = mail.search(None, f'TEXT "{query}"')
            ids = data[0].split()

            if not ids:
                return f"No emails found matching: {query}"

            recent = list(reversed(ids))[:count]
            results = []

            for msg_id in recent:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                subject = _decode_header_value(msg.get("Subject", "(no subject)"))
                sender = _decode_header_value(msg.get("From", "(unknown)"))
                results.append(f"From: {sender} | Subject: {subject}")

            return f"Search '{query}' ({len(results)} results):\n" + "\n".join(results)

    except Exception as e:
        return f"Error searching email: {e}"


# Register tools
registry.register(Tool(
    name="send_email",
    description="Send an email from Igor's Gmail account.",
    parameters={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject line"},
            "body": {"type": "string", "description": "Plain text email body"},
        },
        "required": ["to", "subject", "body"],
    },
    fn=send_email,
))

registry.register(Tool(
    name="read_inbox",
    description="Read recent emails from Igor's Gmail inbox.",
    parameters={
        "type": "object",
        "properties": {
            "count": {"type": "integer", "description": "Number of recent emails to read (default 10)"},
        },
        "required": [],
    },
    fn=read_inbox,
))

registry.register(Tool(
    name="search_email",
    description="Search Igor's Gmail inbox for emails matching a query.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search terms"},
            "count": {"type": "integer", "description": "Max results (default 5)"},
        },
        "required": ["query"],
    },
    fn=search_email,
))

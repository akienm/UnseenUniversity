"""
GoogleSecretaryDispatcher — routes action requests to Google Workspace APIs.

Receives structured action dicts (from GoogleSecretaryDevice.handle_request),
calls the appropriate Google API, and returns a result dict.

Supported actions:
  calendar_create, calendar_read, calendar_delete, calendar_list
  gmail_send, gmail_read, gmail_search, gmail_forward
  tasks_create, tasks_read, tasks_delete, tasks_list

All methods return:
  {"status": "ok",      "result": <data>}
  {"status": "error",   "error": <message>}
  {"status": "escalate","error": <message>, "reason": <why_escalate>}

Auth: credentials come from GoogleSecretaryShim.get_credentials().
The dispatcher is given a credentials provider on construction; it never
manages OAuth itself.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class GoogleSecretaryDispatcher:
    """
    Routes action requests to Google Workspace APIs.

    Requires google-api-python-client + google-auth-oauthlib installed.
    On ImportError, all methods return status="error" with install instructions.
    """

    def __init__(
        self,
        home: Path | str = Path.home() / ".unseen_university" / "google_secretary",
        credentials_provider: Callable | None = None,
    ) -> None:
        self._home = Path(home)
        # credentials_provider() → google.oauth2.credentials.Credentials
        # Injected from GoogleSecretaryShim.get_credentials
        self._creds_fn = credentials_provider
        self._calendar_service = None
        self._gmail_service = None
        self._tasks_service = None

    def _creds(self):
        if self._creds_fn is not None:
            return self._creds_fn()
        return None

    def _build_service(self, name: str, version: str):
        try:
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError(
                "google-api-python-client not installed — "
                "run: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
            )
        creds = self._creds()
        if creds is None:
            raise RuntimeError(
                "No valid Google credentials — configure credentials.json and run OAuth flow"
            )
        return build(name, version, credentials=creds)

    def _calendar(self):
        if self._calendar_service is None:
            self._calendar_service = self._build_service("calendar", "v3")
        return self._calendar_service

    def _gmail(self):
        if self._gmail_service is None:
            self._gmail_service = self._build_service("gmail", "v1")
        return self._gmail_service

    def _tasks(self):
        if self._tasks_service is None:
            self._tasks_service = self._build_service("tasks", "v1")
        return self._tasks_service

    # ── Dispatch router ────────────────────────────────────────────────────────

    def dispatch(self, action: str, params: dict[str, Any]) -> dict:
        """Route action to the appropriate handler. Returns result dict."""
        handlers = {
            # Calendar
            "calendar_create": self._calendar_create,
            "calendar_read": self._calendar_read,
            "calendar_delete": self._calendar_delete,
            "calendar_list": self._calendar_list,
            # Gmail
            "gmail_send": self._gmail_send,
            "gmail_read": self._gmail_read,
            "gmail_search": self._gmail_search,
            "gmail_forward": self._gmail_forward,
            # Tasks
            "tasks_create": self._tasks_create,
            "tasks_read": self._tasks_read,
            "tasks_delete": self._tasks_delete,
            "tasks_list": self._tasks_list,
        }
        handler = handlers.get(action)
        if handler is None:
            return {
                "status": "escalate",
                "error": f"unknown action: {action!r}",
                "reason": f"GoogleSecretary has no handler for action={action!r}",
            }
        try:
            return handler(params)
        except RuntimeError as exc:
            # Auth/config errors
            return {"status": "error", "error": str(exc)}
        except Exception as exc:
            log.error("GoogleSecretaryDispatcher: %s failed: %s", action, exc)
            return {
                "status": "escalate",
                "error": str(exc),
                "reason": f"unexpected error in {action}",
            }

    # ── Calendar ──────────────────────────────────────────────────────────────

    def _calendar_create(self, params: dict) -> dict:
        """Create a calendar event.

        Required params: summary, start (ISO datetime), end (ISO datetime)
        Optional:        calendar_id (default: primary), description, location,
                         attendees (list of email strings)
        """
        svc = self._calendar()
        event = {
            "summary": params["summary"],
            "start": {"dateTime": params["start"], "timeZone": params.get("timezone", "UTC")},
            "end":   {"dateTime": params["end"],   "timeZone": params.get("timezone", "UTC")},
        }
        if "description" in params:
            event["description"] = params["description"]
        if "location" in params:
            event["location"] = params["location"]
        if "attendees" in params:
            event["attendees"] = [{"email": e} for e in params["attendees"]]

        cal_id = params.get("calendar_id", "primary")
        result = svc.events().insert(calendarId=cal_id, body=event).execute()
        log.info("calendar_create: event id=%s", result.get("id"))
        return {"status": "ok", "result": {"id": result.get("id"), "htmlLink": result.get("htmlLink")}}

    def _calendar_read(self, params: dict) -> dict:
        """Read a calendar event by id."""
        svc = self._calendar()
        cal_id = params.get("calendar_id", "primary")
        event_id = params["event_id"]
        result = svc.events().get(calendarId=cal_id, eventId=event_id).execute()
        return {"status": "ok", "result": result}

    def _calendar_delete(self, params: dict) -> dict:
        """Delete a calendar event."""
        svc = self._calendar()
        cal_id = params.get("calendar_id", "primary")
        svc.events().delete(calendarId=cal_id, eventId=params["event_id"]).execute()
        log.info("calendar_delete: event=%s", params["event_id"])
        return {"status": "ok", "result": None}

    def _calendar_list(self, params: dict) -> dict:
        """List upcoming events."""
        svc = self._calendar()
        cal_id = params.get("calendar_id", "primary")
        max_results = int(params.get("max_results", 10))
        time_min = params.get("time_min", datetime.now(timezone.utc).isoformat())
        events = (
            svc.events()
            .list(
                calendarId=cal_id,
                timeMin=time_min,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        items = events.get("items", [])
        return {"status": "ok", "result": items}

    # ── Gmail ─────────────────────────────────────────────────────────────────

    def _build_mime_message(self, to: str, subject: str, body: str, sender: str = "me") -> dict:
        import base64
        from email.mime.text import MIMEText

        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return {"raw": raw}

    def _gmail_send(self, params: dict) -> dict:
        """Send an email.

        Required: to, subject, body
        """
        svc = self._gmail()
        mime = self._build_mime_message(
            to=params["to"],
            subject=params["subject"],
            body=params["body"],
        )
        result = svc.users().messages().send(userId="me", body=mime).execute()
        log.info("gmail_send: message id=%s", result.get("id"))
        return {"status": "ok", "result": {"id": result.get("id")}}

    def _gmail_read(self, params: dict) -> dict:
        """Read an email by message_id."""
        svc = self._gmail()
        result = svc.users().messages().get(
            userId="me",
            id=params["message_id"],
            format=params.get("format", "full"),
        ).execute()
        return {"status": "ok", "result": result}

    def _gmail_search(self, params: dict) -> dict:
        """Search Gmail. Required: query (GMail search syntax)."""
        svc = self._gmail()
        max_results = int(params.get("max_results", 10))
        result = svc.users().messages().list(
            userId="me",
            q=params["query"],
            maxResults=max_results,
        ).execute()
        return {"status": "ok", "result": result.get("messages", [])}

    def _gmail_forward(self, params: dict) -> dict:
        """Forward a message. Required: message_id, to."""
        svc = self._gmail()
        # Fetch original message headers
        orig = svc.users().messages().get(
            userId="me", id=params["message_id"], format="full"
        ).execute()
        headers = {h["name"]: h["value"] for h in orig.get("payload", {}).get("headers", [])}
        subject = f"Fwd: {headers.get('Subject', '')}"
        body = params.get("body", f"---------- Forwarded message ----------\n")
        mime = self._build_mime_message(to=params["to"], subject=subject, body=body)
        result = svc.users().messages().send(userId="me", body=mime).execute()
        log.info("gmail_forward: forwarded %s → %s", params["message_id"], params["to"])
        return {"status": "ok", "result": {"id": result.get("id")}}

    # ── Tasks ─────────────────────────────────────────────────────────────────

    def _tasks_create(self, params: dict) -> dict:
        """Create a task. Required: title. Optional: notes, due (RFC3339)."""
        svc = self._tasks()
        tasklist = params.get("tasklist", "@default")
        body = {"title": params["title"]}
        if "notes" in params:
            body["notes"] = params["notes"]
        if "due" in params:
            body["due"] = params["due"]
        result = svc.tasks().insert(tasklist=tasklist, body=body).execute()
        log.info("tasks_create: task id=%s", result.get("id"))
        return {"status": "ok", "result": {"id": result.get("id")}}

    def _tasks_read(self, params: dict) -> dict:
        """Read a task by id."""
        svc = self._tasks()
        result = svc.tasks().get(
            tasklist=params.get("tasklist", "@default"),
            task=params["task_id"],
        ).execute()
        return {"status": "ok", "result": result}

    def _tasks_delete(self, params: dict) -> dict:
        """Delete a task."""
        svc = self._tasks()
        svc.tasks().delete(
            tasklist=params.get("tasklist", "@default"),
            task=params["task_id"],
        ).execute()
        log.info("tasks_delete: task=%s", params["task_id"])
        return {"status": "ok", "result": None}

    def _tasks_list(self, params: dict) -> dict:
        """List tasks in a tasklist."""
        svc = self._tasks()
        result = svc.tasks().list(
            tasklist=params.get("tasklist", "@default"),
            maxResults=int(params.get("max_results", 20)),
            showCompleted=params.get("show_completed", False),
        ).execute()
        return {"status": "ok", "result": result.get("items", [])}

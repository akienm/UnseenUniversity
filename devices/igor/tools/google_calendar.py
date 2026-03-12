"""
Google Calendar + Tasks tools for Igor.

Covers Igor's own Google account (theigorsigor@gmail.com) by default.
Employer calendars: set GOOGLE_CREDENTIALS_PATH / GOOGLE_TOKEN_PATH to the
employer-provided credentials. Providing credentials = consent to access.

OAuth2 setup (one-time per account):
  1. In Google Cloud Console: enable Calendar API + Tasks API
  2. Create OAuth2 credentials (Desktop app), download as credentials.json
  3. Place at GOOGLE_CREDENTIALS_PATH (default: ~/.TheIgors/igor_wild_0001/google_credentials.json)
  4. Run once: python -c "from igor.tools.google_calendar import _get_service; _get_service('calendar','v3')"
     → browser opens for consent → token saved to GOOGLE_TOKEN_PATH
  5. Set IGOR_CALENDAR_ENABLED=true in .env

Env vars:
  IGOR_CALENDAR_ENABLED        — gate (default false)
  GOOGLE_CREDENTIALS_PATH      — OAuth client secret file
  GOOGLE_TOKEN_PATH            — saved token file (auto-created on first auth)
  GOOGLE_CALENDAR_ID           — target calendar (default "primary")
  GOOGLE_TASKS_LIST_ID         — target task list (default "@default")
  GOOGLE_CALENDAR_LOOKAHEAD_MINS — how far ahead CalendarSource looks (default 30)
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .registry import Tool, registry

# ── Paths + gates ─────────────────────────────────────────────────────────────

_INSTANCE_DIR = Path.home() / ".TheIgors" / "igor_wild_0001"
_DEFAULT_CREDS = _INSTANCE_DIR / "google_credentials.json"
_DEFAULT_TOKEN = _INSTANCE_DIR / "google_token.json"

_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/contacts",
]


def _enabled() -> bool:
    return os.getenv("IGOR_CALENDAR_ENABLED", "false").lower() == "true"


def _creds_path() -> Path:
    return Path(os.getenv("GOOGLE_CREDENTIALS_PATH", str(_DEFAULT_CREDS)))


def _token_path() -> Path:
    return Path(os.getenv("GOOGLE_TOKEN_PATH", str(_DEFAULT_TOKEN)))


def _calendar_id() -> str:
    return os.getenv("GOOGLE_CALENDAR_ID", "primary")


def _tasks_list_id() -> str:
    return os.getenv("GOOGLE_TASKS_LIST_ID", "@default")


# ── OAuth helper ──────────────────────────────────────────────────────────────

def _get_service(api: str, version: str, scopes: list | None = None):
    """
    Build and return an authenticated Google API service.
    Loads token from file; refreshes or re-authenticates as needed.
    On first run (no token), opens browser for consent and saves token.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "Google API client libs not installed. Run:\n"
            "  venv/bin/pip install google-api-python-client google-auth-httplib2 "
            "google-auth-oauthlib"
        )

    _scopes = scopes or _SCOPES
    creds = None
    token_path = _token_path()

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds_path = _creds_path()
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"Google credentials not found at {creds_path}.\n"
                    "Download OAuth2 client credentials from Google Cloud Console "
                    "and place at that path (or set GOOGLE_CREDENTIALS_PATH)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), _scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build(api, version, credentials=creds)


# ── Event tools ───────────────────────────────────────────────────────────────

def create_event(
    title: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    attendees: list[str] | None = None,
    calendar_id: str | None = None,
) -> str:
    """
    Create a calendar event. Times in ISO 8601 (e.g. '2026-03-12T14:00:00-07:00').
    Returns event ID on success.
    """
    if not _enabled():
        return "CALENDAR_DISABLED: set IGOR_CALENDAR_ENABLED=true in .env"
    try:
        svc = _get_service("calendar", "v3")
        body: dict = {
            "summary": title,
            "start": {"dateTime": start_iso, "timeZone": "America/Los_Angeles"},
            "end":   {"dateTime": end_iso,   "timeZone": "America/Los_Angeles"},
        }
        if description:
            body["description"] = description
        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]
        event = svc.events().insert(
            calendarId=calendar_id or _calendar_id(), body=body
        ).execute()
        return f"created:{event['id']}"
    except Exception as e:
        return f"error:{e}"


def list_events(
    time_min_iso: str | None = None,
    time_max_iso: str | None = None,
    max_results: int = 10,
    calendar_id: str | None = None,
) -> list[dict]:
    """
    List upcoming calendar events. Defaults to next 24 hours.
    Returns list of {id, title, start, end, description}.
    """
    if not _enabled():
        return [{"error": "CALENDAR_DISABLED"}]
    try:
        svc = _get_service("calendar", "v3")
        now = datetime.now(timezone.utc)
        t_min = time_min_iso or now.isoformat()
        t_max = time_max_iso or (now + timedelta(hours=24)).isoformat()
        result = svc.events().list(
            calendarId=calendar_id or _calendar_id(),
            timeMin=t_min, timeMax=t_max,
            maxResults=max_results, singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = []
        for e in result.get("items", []):
            events.append({
                "id":          e["id"],
                "title":       e.get("summary", "(no title)"),
                "start":       e["start"].get("dateTime", e["start"].get("date", "")),
                "end":         e["end"].get("dateTime",   e["end"].get("date", "")),
                "description": e.get("description", ""),
            })
        return events
    except Exception as e:
        return [{"error": str(e)}]


def update_event(
    event_id: str,
    title: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    description: str | None = None,
    calendar_id: str | None = None,
) -> str:
    """Update an existing calendar event. Only provided fields are changed."""
    if not _enabled():
        return "CALENDAR_DISABLED"
    try:
        svc   = _get_service("calendar", "v3")
        cal_id = calendar_id or _calendar_id()
        event  = svc.events().get(calendarId=cal_id, eventId=event_id).execute()
        if title:
            event["summary"] = title
        if start_iso:
            event["start"] = {"dateTime": start_iso, "timeZone": "America/Los_Angeles"}
        if end_iso:
            event["end"] = {"dateTime": end_iso, "timeZone": "America/Los_Angeles"}
        if description is not None:
            event["description"] = description
        updated = svc.events().update(
            calendarId=cal_id, eventId=event_id, body=event
        ).execute()
        return f"updated:{updated['id']}"
    except Exception as e:
        return f"error:{e}"


def delete_event(event_id: str, calendar_id: str | None = None) -> str:
    """Delete a calendar event by ID."""
    if not _enabled():
        return "CALENDAR_DISABLED"
    try:
        svc = _get_service("calendar", "v3")
        svc.events().delete(
            calendarId=calendar_id or _calendar_id(), eventId=event_id
        ).execute()
        return f"deleted:{event_id}"
    except Exception as e:
        return f"error:{e}"


# ── Task tools ────────────────────────────────────────────────────────────────

def create_task(
    title: str,
    notes: str = "",
    due_iso: str | None = None,
    tasklist_id: str | None = None,
) -> str:
    """
    Create a Google Task. due_iso is a date string e.g. '2026-03-13T00:00:00.000Z'.
    Returns task ID on success.
    """
    if not _enabled():
        return "CALENDAR_DISABLED"
    try:
        svc  = _get_service("tasks", "v1")
        body: dict = {"title": title}
        if notes:
            body["notes"] = notes
        if due_iso:
            body["due"] = due_iso
        task = svc.tasks().insert(
            tasklist=tasklist_id or _tasks_list_id(), body=body
        ).execute()
        return f"created:{task['id']}"
    except Exception as e:
        return f"error:{e}"


def list_tasks(
    show_completed: bool = False,
    due_max_iso: str | None = None,
    tasklist_id: str | None = None,
) -> list[dict]:
    """
    List tasks. Returns list of {id, title, notes, due, status}.
    """
    if not _enabled():
        return [{"error": "CALENDAR_DISABLED"}]
    try:
        svc = _get_service("tasks", "v1")
        kwargs: dict = {
            "tasklist": tasklist_id or _tasks_list_id(),
            "showCompleted": show_completed,
            "showHidden": False,
        }
        if due_max_iso:
            kwargs["dueMax"] = due_max_iso
        result = svc.tasks().list(**kwargs).execute()
        tasks = []
        for t in result.get("items", []):
            tasks.append({
                "id":     t["id"],
                "title":  t.get("title", "(no title)"),
                "notes":  t.get("notes", ""),
                "due":    t.get("due", ""),
                "status": t.get("status", "needsAction"),
            })
        return tasks
    except Exception as e:
        return [{"error": str(e)}]


def complete_task(task_id: str, tasklist_id: str | None = None) -> str:
    """Mark a task as completed."""
    if not _enabled():
        return "CALENDAR_DISABLED"
    try:
        svc = _get_service("tasks", "v1")
        tl  = tasklist_id or _tasks_list_id()
        task = svc.tasks().get(tasklist=tl, task=task_id).execute()
        task["status"] = "completed"
        updated = svc.tasks().update(tasklist=tl, task=task_id, body=task).execute()
        return f"completed:{updated['id']}"
    except Exception as e:
        return f"error:{e}"


def delete_task(task_id: str, tasklist_id: str | None = None) -> str:
    """Delete a task by ID."""
    if not _enabled():
        return "CALENDAR_DISABLED"
    try:
        svc = _get_service("tasks", "v1")
        svc.tasks().delete(
            tasklist=tasklist_id or _tasks_list_id(), task=task_id
        ).execute()
        return f"deleted:{task_id}"
    except Exception as e:
        return f"error:{e}"


# ── Tool registration ─────────────────────────────────────────────────────────

registry.register(Tool(
    name="create_calendar_event",
    description="Create a calendar event. start_iso and end_iso are ISO 8601 datetimes.",
    parameters={
        "type": "object",
        "properties": {
            "title":       {"type": "string"},
            "start_iso":   {"type": "string", "description": "ISO 8601 start datetime"},
            "end_iso":     {"type": "string", "description": "ISO 8601 end datetime"},
            "description": {"type": "string"},
            "attendees":   {"type": "array", "items": {"type": "string"}, "description": "email addresses"},
        },
        "required": ["title", "start_iso", "end_iso"],
    },
    fn=create_event,
))

registry.register(Tool(
    name="list_calendar_events",
    description="List upcoming calendar events within a time range.",
    parameters={
        "type": "object",
        "properties": {
            "time_min_iso":  {"type": "string", "description": "ISO 8601 start of range"},
            "time_max_iso":  {"type": "string", "description": "ISO 8601 end of range"},
            "max_results":   {"type": "integer", "default": 10},
        },
    },
    fn=list_events,
))

registry.register(Tool(
    name="update_calendar_event",
    description="Update an existing calendar event. Only provided fields are changed.",
    parameters={
        "type": "object",
        "properties": {
            "event_id":    {"type": "string"},
            "title":       {"type": "string"},
            "start_iso":   {"type": "string"},
            "end_iso":     {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["event_id"],
    },
    fn=update_event,
))

registry.register(Tool(
    name="delete_calendar_event",
    description="Delete a calendar event by ID.",
    parameters={
        "type": "object",
        "properties": {"event_id": {"type": "string"}},
        "required": ["event_id"],
    },
    fn=delete_event,
))

registry.register(Tool(
    name="create_task",
    description="Create a Google Task (no fixed time required; due_iso is optional date).",
    parameters={
        "type": "object",
        "properties": {
            "title":    {"type": "string"},
            "notes":    {"type": "string"},
            "due_iso":  {"type": "string", "description": "ISO 8601 due date"},
        },
        "required": ["title"],
    },
    fn=create_task,
))

registry.register(Tool(
    name="list_tasks",
    description="List pending Google Tasks.",
    parameters={
        "type": "object",
        "properties": {
            "show_completed": {"type": "boolean", "default": False},
            "due_max_iso":    {"type": "string"},
        },
    },
    fn=list_tasks,
))

registry.register(Tool(
    name="complete_task",
    description="Mark a Google Task as completed.",
    parameters={
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    },
    fn=complete_task,
))

registry.register(Tool(
    name="delete_task",
    description="Delete a Google Task.",
    parameters={
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    },
    fn=delete_task,
))

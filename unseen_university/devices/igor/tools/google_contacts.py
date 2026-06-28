import logging

"""
Google Contacts tools for Igor — via the People API.

Igor stores contact info in two places:
  1. His own DB (FACTUAL memory, always) — fast local lookup, survives offline
  2. Google Contacts (optionally, same OAuth as Calendar) — syncs across devices

When Igor encounters someone's contact info — from email, calendar invites,
conversations, or web searches — he stores it. This is how humans use contacts.

Same OAuth credentials as google_calendar.py (GOOGLE_CREDENTIALS_PATH).
Requires scope: https://www.googleapis.com/auth/contacts
Gate: IGOR_CALENDAR_ENABLED=true (shared credential gate)

Note: after adding this scope, delete google_token.json and re-run auth to
get a new token with the contacts scope included.
"""

import os
from pathlib import Path
from typing import Optional

from unseen_university.devices.igor.tools.registry import Tool, registry
from .google_calendar import _get_service, _enabled

_PEOPLE_SCOPES = ["https://www.googleapis.com/auth/contacts"]


def _people_svc():
    return _get_service(
        "people",
        "v1",
        scopes=[
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/tasks",
            "https://www.googleapis.com/auth/contacts",
        ],
    )


# ── Contact tools ─────────────────────────────────────────────────────────────


def create_contact(
    name: str,
    email: str = "",
    phone: str = "",
    organization: str = "",
    notes: str = "",
) -> str:
    """
    Create a Google Contact. Returns resource_name (e.g. 'people/c12345') on success.
    Also stores a FACTUAL memory in Igor's DB for fast local lookup.
    """
    _store_contact_memory(
        name=name, email=email, phone=phone, organization=organization, notes=notes
    )

    if not _enabled():
        return f"stored_locally_only:{_contact_id(name, email)}"
    try:
        svc = _people_svc()
        body: dict = {"names": [{"displayName": name}]}
        if email:
            body["emailAddresses"] = [{"value": email}]
        if phone:
            body["phoneNumbers"] = [{"value": phone}]
        if organization:
            body["organizations"] = [{"name": organization}]
        if notes:
            body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]
        person = svc.people().createContact(body=body).execute()
        return f"created:{person['resourceName']}"
    except Exception as e:
        return f"stored_locally|google_error:{e}"


def search_contacts(query: str, max_results: int = 5) -> list[dict]:
    """
    Search Google Contacts by name or email.
    Falls back to DB memory search if Google unavailable.
    Returns list of {resource_name, name, email, phone, organization}.
    """
    if not _enabled():
        return _search_contact_memories(query)
    try:
        svc = _people_svc()
        result = (
            svc.people()
            .searchContacts(
                query=query,
                readMask="names,emailAddresses,phoneNumbers,organizations,biographies",
                pageSize=max_results,
            )
            .execute()
        )
        contacts = []
        for r in result.get("results", []):
            p = r.get("person", {})
            contacts.append(_parse_person(p))
        return contacts or _search_contact_memories(query)
    except Exception as e:
        return _search_contact_memories(query) or [{"error": str(e)}]


def get_contact(resource_name: str) -> dict:
    """
    Get a Google Contact by resource_name (e.g. 'people/c12345').
    """
    if not _enabled():
        return {"error": "CALENDAR_DISABLED"}
    try:
        svc = _people_svc()
        person = (
            svc.people()
            .get(
                resourceName=resource_name,
                personFields="names,emailAddresses,phoneNumbers,organizations,biographies",
            )
            .execute()
        )
        return _parse_person(person)
    except Exception as e:
        return {"error": str(e)}


def update_contact(
    resource_name: str,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    organization: str | None = None,
    notes: str | None = None,
) -> str:
    """Update fields on an existing Google Contact."""
    if not _enabled():
        return "CALENDAR_DISABLED"
    try:
        svc = _people_svc()
        person = (
            svc.people()
            .get(
                resourceName=resource_name,
                personFields="names,emailAddresses,phoneNumbers,organizations,biographies,metadata",
            )
            .execute()
        )
        update_mask_fields = []
        if name:
            person["names"] = [{"displayName": name}]
            update_mask_fields.append("names")
        if email:
            person["emailAddresses"] = [{"value": email}]
            update_mask_fields.append("emailAddresses")
        if phone:
            person["phoneNumbers"] = [{"value": phone}]
            update_mask_fields.append("phoneNumbers")
        if organization:
            person["organizations"] = [{"name": organization}]
            update_mask_fields.append("organizations")
        if notes:
            person["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]
            update_mask_fields.append("biographies")
        updated = (
            svc.people()
            .updateContact(
                resourceName=resource_name,
                updatePersonFields=",".join(update_mask_fields),
                body=person,
            )
            .execute()
        )
        return f"updated:{updated['resourceName']}"
    except Exception as e:
        return f"error:{e}"


# ── DB memory helpers ─────────────────────────────────────────────────────────


def _contact_id(name: str, email: str) -> str:
    """Stable memory ID from name + email."""
    import hashlib

    key = f"{name.lower().strip()}|{email.lower().strip()}"
    return "CONTACT_" + hashlib.sha256(key.encode()).hexdigest()[:10].upper()


def _store_contact_memory(
    name: str,
    email: str = "",
    phone: str = "",
    organization: str = "",
    notes: str = "",
) -> None:
    """Store contact as FACTUAL memory in Igor's DB."""
    try:
        import sys
        from pathlib import Path as _P

        sys.path.insert(0, str(_P(__file__).parent.parent.parent.parent))
        from igor.memory.cortex import Cortex
        from igor.memory.models import Memory, MemoryType
        from igor.paths import paths as _paths

        cortex = Cortex(None)

        parts = [f"Contact: {name}"]
        if email:
            parts.append(f"email: {email}")
        if phone:
            parts.append(f"phone: {phone}")
        if organization:
            parts.append(f"org: {organization}")
        if notes:
            parts.append(f"notes: {notes}")
        narrative = ". ".join(parts) + "."

        mem = Memory(
            id=_contact_id(name, email),
            narrative=narrative,
            memory_type=MemoryType.FACTUAL,
            activation_count=0,
            valence=0.6,
            metadata={
                "tags": ["contact", "person"],
                "name": name,
                "email": email,
                "phone": phone,
                "organization": organization,
                "portable": True,
            },
        )
        cortex.store(mem)
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in devices/igor/tools/google_contacts.py: %s", _bare_e
        )


def _search_contact_memories(query: str) -> list[dict]:
    """Fall back: search Igor's DB for contact memories matching query."""
    try:
        import sys
        from pathlib import Path as _P

        sys.path.insert(0, str(_P(__file__).parent.parent.parent.parent))
        from igor.memory.cortex import Cortex
        from igor.paths import paths as _paths

        cortex = Cortex(None)
        results = cortex.search(query, limit=5, min_score=0.3)
        contacts = []
        for m in results:
            if "contact" in m.metadata.get("tags", []):
                contacts.append(
                    {
                        "resource_name": m.id,
                        "name": m.metadata.get("name", ""),
                        "email": m.metadata.get("email", ""),
                        "phone": m.metadata.get("phone", ""),
                        "organization": m.metadata.get("organization", ""),
                    }
                )
        return contacts
    except Exception:
        return []


def _parse_person(p: dict) -> dict:
    """Extract flat contact dict from a People API Person resource."""
    name = p.get("names", [{}])[0].get("displayName", "")
    email = p.get("emailAddresses", [{}])[0].get("value", "")
    phone = p.get("phoneNumbers", [{}])[0].get("value", "")
    org = p.get("organizations", [{}])[0].get("name", "")
    notes = p.get("biographies", [{}])[0].get("value", "")
    return {
        "resource_name": p.get("resourceName", ""),
        "name": name,
        "email": email,
        "phone": phone,
        "organization": org,
        "notes": notes,
    }


# ── Tool registration ─────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="create_contact",
        description=(
            "Store a person's contact info — always saves to Igor's DB; also syncs to "
            "Google Contacts if IGOR_CALENDAR_ENABLED=true."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "organization": {"type": "string"},
                "notes": {
                    "type": "string",
                    "description": "context: how Igor knows them, role, etc.",
                },
            },
            "required": ["name"],
        },
        fn=create_contact,
    )
)

registry.register(
    Tool(
        name="search_contacts",
        description="Search contacts by name or email. Checks Google Contacts then falls back to Igor's DB.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        fn=search_contacts,
    )
)

registry.register(
    Tool(
        name="update_contact",
        description="Update an existing Google Contact by resource_name.",
        parameters={
            "type": "object",
            "properties": {
                "resource_name": {"type": "string"},
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "organization": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["resource_name"],
        },
        fn=update_contact,
    )
)

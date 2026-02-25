"""
Confluence tools - read and write Confluence pages via REST API v2.
Uses API token authentication (email + token).

Required .env vars:
  ConfluenceAPIKey   - Atlassian API token
  CONFLUENCE_EMAIL   - Atlassian account email (defaults to GMAIL_USER)
  CONFLUENCE_DOMAIN  - e.g. "mysite.atlassian.net"
"""

import os
import requests
from requests.auth import HTTPBasicAuth
from .registry import Tool, registry


def _client():
    """Return (base_url, auth) for Confluence API calls."""
    token = os.getenv("ConfluenceAPIKey", "")
    email = os.getenv("CONFLUENCE_EMAIL") or os.getenv("GMAIL_USER", "")
    domain = os.getenv("CONFLUENCE_DOMAIN", "")

    if not token:
        raise ValueError("ConfluenceAPIKey must be set in .env")
    if not email:
        raise ValueError("CONFLUENCE_EMAIL (or GMAIL_USER) must be set in .env")
    if not domain:
        raise ValueError("CONFLUENCE_DOMAIN must be set in .env (e.g. mysite.atlassian.net)")

    base = f"https://{domain}/wiki/api/v2"
    auth = HTTPBasicAuth(email, token)
    return base, auth


def confluence_get_page(page_id: str = "", title: str = "", space_key: str = "") -> str:
    """Fetch a Confluence page by ID, or by title+space_key."""
    try:
        base, auth = _client()
        headers = {"Accept": "application/json"}

        if page_id:
            url = f"{base}/pages/{page_id}"
            params = {"body-format": "storage"}
            r = requests.get(url, auth=auth, headers=headers, params=params)
        elif title and space_key:
            # Search by title in space
            url = f"{base}/pages"
            params = {"title": title, "body-format": "storage"}
            # Need space-id; first look up space
            space_url = f"{base}/spaces"
            sr = requests.get(space_url, auth=auth, headers=headers, params={"keys": space_key})
            sr.raise_for_status()
            spaces = sr.json().get("results", [])
            if not spaces:
                return f"Space '{space_key}' not found."
            params["space-id"] = spaces[0]["id"]
            r = requests.get(url, auth=auth, headers=headers, params=params)
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return f"No page titled '{title}' found in space '{space_key}'."
            page = results[0]
            body_html = page.get("body", {}).get("storage", {}).get("value", "(no body)")
            return (
                f"Page: {page['title']} (ID: {page['id']})\n"
                f"Space: {space_key}\n"
                f"URL: https://{os.getenv('CONFLUENCE_DOMAIN')}/wiki{page.get('_links', {}).get('webui', '')}\n\n"
                f"Body (storage format):\n{body_html[:3000]}"
            )
        else:
            return "Provide either page_id, or both title and space_key."

        r.raise_for_status()
        page = r.json()
        body_html = page.get("body", {}).get("storage", {}).get("value", "(no body)")
        domain = os.getenv("CONFLUENCE_DOMAIN", "")
        return (
            f"Page: {page['title']} (ID: {page['id']})\n"
            f"Version: {page.get('version', {}).get('number', '?')}\n"
            f"URL: https://{domain}/wiki{page.get('_links', {}).get('webui', '')}\n\n"
            f"Body (storage format):\n{body_html[:3000]}"
        )

    except Exception as e:
        return f"Error fetching page: {e}"


def confluence_search(cql: str, limit: int = 10) -> str:
    """Search Confluence using CQL (Confluence Query Language)."""
    try:
        base, auth = _client()
        # v1 search endpoint — CQL search isn't in v2 yet
        domain = os.getenv("CONFLUENCE_DOMAIN", "")
        url = f"https://{domain}/wiki/rest/api/content/search"
        params = {"cql": cql, "limit": limit, "expand": "space,version"}
        r = requests.get(url, auth=auth, headers={"Accept": "application/json"}, params=params)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if not results:
            return f"No results for CQL: {cql}"

        lines = [f"Search: {cql} ({len(results)} results)\n"]
        for item in results:
            space = item.get("space", {}).get("key", "?")
            title = item.get("title", "(untitled)")
            pid = item.get("id", "?")
            webui = item.get("_links", {}).get("webui", "")
            lines.append(f"  [{space}] {title} (ID: {pid})\n    https://{domain}/wiki{webui}")
        return "\n".join(lines)

    except Exception as e:
        return f"Error searching Confluence: {e}"


def confluence_create_page(space_key: str, title: str, body_html: str, parent_id: str = "") -> str:
    """Create a new Confluence page in the given space."""
    try:
        base, auth = _client()
        domain = os.getenv("CONFLUENCE_DOMAIN", "")

        # Look up space ID
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        sr = requests.get(f"{base}/spaces", auth=auth, headers=headers, params={"keys": space_key})
        sr.raise_for_status()
        spaces = sr.json().get("results", [])
        if not spaces:
            return f"Space '{space_key}' not found."
        space_id = spaces[0]["id"]

        payload = {
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {
                "representation": "storage",
                "value": body_html,
            },
        }
        if parent_id:
            payload["parentId"] = parent_id

        r = requests.post(f"{base}/pages", auth=auth, headers=headers, json=payload)
        r.raise_for_status()
        page = r.json()
        webui = page.get("_links", {}).get("webui", "")
        return (
            f"Created: '{title}' (ID: {page['id']})\n"
            f"URL: https://{domain}/wiki{webui}"
        )

    except Exception as e:
        return f"Error creating page: {e}"


def confluence_update_page(page_id: str, title: str, body_html: str, version_comment: str = "") -> str:
    """Update an existing Confluence page (fetches current version automatically)."""
    try:
        base, auth = _client()
        domain = os.getenv("CONFLUENCE_DOMAIN", "")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        # Get current version number
        r = requests.get(f"{base}/pages/{page_id}", auth=auth, headers=headers)
        r.raise_for_status()
        current = r.json()
        current_version = current.get("version", {}).get("number", 1)
        space_id = current.get("spaceId", "")

        payload = {
            "id": page_id,
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {
                "representation": "storage",
                "value": body_html,
            },
            "version": {
                "number": current_version + 1,
                "message": version_comment or "Updated by Igor",
            },
        }

        r = requests.put(f"{base}/pages/{page_id}", auth=auth, headers=headers, json=payload)
        r.raise_for_status()
        page = r.json()
        webui = page.get("_links", {}).get("webui", "")
        return (
            f"Updated: '{title}' → version {current_version + 1}\n"
            f"URL: https://{domain}/wiki{webui}"
        )

    except Exception as e:
        return f"Error updating page: {e}"


# ── Register all tools ──────────────────────────────────────────────────────

registry.register(Tool(
    name="confluence_get_page",
    description="Fetch a Confluence page by ID, or by title + space key. Returns title, URL, and body content.",
    parameters={
        "type": "object",
        "properties": {
            "page_id": {"type": "string", "description": "Numeric Confluence page ID"},
            "title": {"type": "string", "description": "Page title (use with space_key)"},
            "space_key": {"type": "string", "description": "Space key (e.g. 'ENG', 'PROJ')"},
        },
        "required": [],
    },
    fn=confluence_get_page,
))

registry.register(Tool(
    name="confluence_search",
    description="Search Confluence using CQL (Confluence Query Language). E.g. 'text ~ \"onboarding\" AND space = \"ENG\"'",
    parameters={
        "type": "object",
        "properties": {
            "cql": {"type": "string", "description": "CQL query string"},
            "limit": {"type": "integer", "description": "Max results (default 10)"},
        },
        "required": ["cql"],
    },
    fn=confluence_search,
))

registry.register(Tool(
    name="confluence_create_page",
    description="Create a new Confluence page in a space. Body is Confluence storage format (HTML-like XHTML).",
    parameters={
        "type": "object",
        "properties": {
            "space_key": {"type": "string", "description": "Target space key"},
            "title": {"type": "string", "description": "Page title"},
            "body_html": {"type": "string", "description": "Page body in Confluence storage format"},
            "parent_id": {"type": "string", "description": "Optional parent page ID"},
        },
        "required": ["space_key", "title", "body_html"],
    },
    fn=confluence_create_page,
))

registry.register(Tool(
    name="confluence_update_page",
    description="Update an existing Confluence page. Automatically increments version. Body is storage format.",
    parameters={
        "type": "object",
        "properties": {
            "page_id": {"type": "string", "description": "Numeric page ID"},
            "title": {"type": "string", "description": "Page title (can be unchanged)"},
            "body_html": {"type": "string", "description": "New body in Confluence storage format"},
            "version_comment": {"type": "string", "description": "Optional version note"},
        },
        "required": ["page_id", "title", "body_html"],
    },
    fn=confluence_update_page,
))

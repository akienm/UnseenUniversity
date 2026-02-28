"""
GitHub work order system — creates and manages GitHub issues as work orders.

Uses urllib.request (no extra dependencies required).

Env vars:
    GITHUB_API_KEY — personal access token with repo scope
    GITHUB_REPO    — e.g. akienm/TheIgors
"""

import json
import os
import urllib.request
import urllib.error
from .registry import Tool, registry


def _repo() -> str:
    r = os.getenv("GITHUB_REPO", "").strip()
    if not r:
        raise RuntimeError("GITHUB_REPO not set (format: owner/repo)")
    return r


def _gh_api(method: str, path: str, data: dict | None = None):
    """GitHub REST API call. Returns parsed JSON. Raises RuntimeError on failure."""
    token = os.getenv("GITHUB_API_KEY", "").strip()
    if not token:
        raise RuntimeError("GITHUB_API_KEY not set")
    repo = _repo()
    url = f"https://api.github.com{path.format(repo=repo)}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Igor-Wild-Agent",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_text = e.read().decode()[:200]
        raise RuntimeError(f"GitHub {method} {url} → {e.code}: {err_text}")


# ── Tool functions ─────────────────────────────────────────────────────────────

def create_work_order(title: str, description: str, labels: list | None = None) -> str:
    """Create a GitHub issue as a work order."""
    try:
        repo = _repo()
        if labels is None:
            labels = ["work-order"]
        result = _gh_api("POST", f"/repos/{repo}/issues", {
            "title": title,
            "body": description,
            "labels": labels,
        })
        number = result["number"]
        url = result["html_url"]
        return f"Work order #{number} created: {title}\n{url}"
    except Exception as e:
        return f"Error creating work order: {e}"


def update_work_order(number: int, status: str, comment: str) -> str:
    """Post a status update comment on an existing work order."""
    try:
        valid = ("in-progress", "blocked", "complete")
        if status not in valid:
            return f"Invalid status '{status}'. Use: {', '.join(valid)}"
        repo = _repo()
        body = f"**Status: {status}**\n\n{comment}"
        _gh_api("POST", f"/repos/{repo}/issues/{number}/comments", {"body": body})
        return f"Work order #{number} updated: status={status}"
    except Exception as e:
        return f"Error updating work order #{number}: {e}"


def close_work_order(number: int, resolution: str) -> str:
    """Close a work order with a resolution comment."""
    try:
        repo = _repo()
        _gh_api("POST", f"/repos/{repo}/issues/{number}/comments",
                {"body": f"**Resolution:**\n\n{resolution}"})
        _gh_api("PATCH", f"/repos/{repo}/issues/{number}", {"state": "closed"})
        return f"Work order #{number} closed."
    except Exception as e:
        return f"Error closing work order #{number}: {e}"


def list_work_orders(state: str = "open", limit: int = 10) -> str:
    """List work orders (GitHub issues labeled 'work-order')."""
    try:
        if state not in ("open", "closed", "all"):
            state = "open"
        repo = _repo()
        issues = _gh_api(
            "GET",
            f"/repos/{repo}/issues?state={state}&labels=work-order&per_page={limit}",
        )
        if not issues:
            return f"No {state} work orders found."
        lines = [f"Work orders ({state}, {len(issues)} shown):"]
        for issue in issues:
            tag = "[closed]" if issue["state"] == "closed" else "[open] "
            lines.append(f"  #{issue['number']} {tag} {issue['title']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing work orders: {e}"


def get_work_order(number: int) -> str:
    """Get full detail on a work order including all comments."""
    try:
        repo = _repo()
        issue = _gh_api("GET", f"/repos/{repo}/issues/{number}")
        comments = _gh_api("GET", f"/repos/{repo}/issues/{number}/comments")
        lines = [
            f"Work order #{issue['number']}: {issue['title']}",
            f"State:   {issue['state']}",
            f"URL:     {issue['html_url']}",
            f"Created: {issue['created_at'][:10]}",
            f"\nDescription:\n{issue.get('body') or '(none)'}",
        ]
        if comments:
            lines.append(f"\nComments ({len(comments)}):")
            for c in comments:
                lines.append(f"  [{c['created_at'][:10]}] {c['body'][:200]}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting work order #{number}: {e}"


# ── Register tools ─────────────────────────────────────────────────────────────

registry.register(Tool(
    name="create_work_order",
    description=(
        "Create a GitHub issue as a work order. Use when akien describes a task or pastes "
        "a change block. After creating, confirm: 'that's work order #N: {title} - {url}'. "
        "On completion, close the issue with close_work_order and reference '#N' in any commit."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short imperative title"},
            "description": {"type": "string", "description": "Full task description"},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Labels (default: ['work-order'])",
            },
        },
        "required": ["title", "description"],
    },
    fn=create_work_order,
))

registry.register(Tool(
    name="update_work_order",
    description="Post a status update comment on a work order.",
    parameters={
        "type": "object",
        "properties": {
            "number": {"type": "integer", "description": "Issue number"},
            "status": {"type": "string", "description": "in-progress | blocked | complete"},
            "comment": {"type": "string", "description": "Update comment text"},
        },
        "required": ["number", "status", "comment"],
    },
    fn=update_work_order,
))

registry.register(Tool(
    name="close_work_order",
    description="Close a work order with a resolution summary.",
    parameters={
        "type": "object",
        "properties": {
            "number": {"type": "integer", "description": "Issue number"},
            "resolution": {"type": "string", "description": "What was done to resolve it"},
        },
        "required": ["number", "resolution"],
    },
    fn=close_work_order,
))

registry.register(Tool(
    name="list_work_orders",
    description="List GitHub work orders. state: open|closed|all, limit default 10.",
    parameters={
        "type": "object",
        "properties": {
            "state": {"type": "string", "description": "open | closed | all (default: open)"},
            "limit": {"type": "integer", "description": "Max results to return (default: 10)"},
        },
        "required": [],
    },
    fn=list_work_orders,
))

registry.register(Tool(
    name="get_work_order",
    description="Get full detail on a specific work order including all comments.",
    parameters={
        "type": "object",
        "properties": {
            "number": {"type": "integer", "description": "Issue number"},
        },
        "required": ["number"],
    },
    fn=get_work_order,
))

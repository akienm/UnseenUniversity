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


def sync_github_issues(state: str = "all", limit: int = 50, **_) -> str:
    """
    Sync GitHub issues into blob storage (#87).

    Uses synced_at metadata for incremental refresh — only fetches issues updated
    since the last sync. First run does a full fetch. Upserts (no duplicates).

    state: open | closed | all (default: all)
    limit: max issues to fetch per sync (default: 50)
    """
    from datetime import datetime as _dt
    import os as _os

    db_path = _os.getenv("IGOR_DB_PATH", "memory/igor.db")
    from pathlib import Path as _Path
    from ..memory.cortex import Cortex as _Cortex
    cortex = _Cortex(_Path(db_path))

    # Find last synced_at across all github+issue blobs
    try:
        existing = cortex.search_by_tags(["github", "issue"])
        sync_times = [
            b.get("created_at", "")
            for b in existing
        ]
        # Use metadata synced_at if available
        import json as _json
        sync_times_meta = []
        for b in existing:
            mem = cortex.get(b["memory_id"])
            if mem and mem.metadata.get("synced_at"):
                sync_times_meta.append(mem.metadata["synced_at"])
        last_sync = max(sync_times_meta) if sync_times_meta else None
    except Exception:
        last_sync = None

    # Fetch issues (incremental if last_sync available)
    try:
        repo = _repo()
        valid_states = ("open", "closed", "all")
        if state not in valid_states:
            state = "all"
        since_param = f"&since={last_sync}" if last_sync else ""
        issues = _gh_api(
            "GET",
            f"/repos/{repo}/issues?state={state}&per_page={limit}&sort=updated&direction=desc{since_param}",
        )
    except Exception as e:
        return f"Error fetching GitHub issues: {e}"

    if not issues:
        return f"No issues updated since {last_sync or 'beginning'}."

    created_count = 0
    updated_count = 0
    now = _dt.now().isoformat()

    for issue in issues:
        num = issue["number"]
        title = issue["title"]
        body = issue.get("body") or ""
        state_label = issue["state"]
        labels = [lb["name"] for lb in issue.get("labels", [])]
        updated_at = issue.get("updated_at", "")

        narrative = f"GitHub #{num}: {title} [{state_label}]"
        content = (
            f"# GitHub Issue #{num}: {title}\n"
            f"State: {state_label}\n"
            f"Labels: {', '.join(labels) or 'none'}\n"
            f"Updated: {updated_at[:10]}\n"
            f"URL: {issue.get('html_url', '')}\n\n"
            f"{body}"
        )
        tags = ["github", "issue"] + labels
        source_id = f"github_issue_{num}"

        _, was_created = cortex.upsert_blob(
            narrative=narrative,
            content=content,
            tags=tags,
            source_id=source_id,
            extra_metadata={"issue_number": num, "issue_state": state_label, "synced_at": now},
        )
        if was_created:
            created_count += 1
        else:
            updated_count += 1

    since_str = f"since {last_sync[:10]}" if last_sync else "(full sync)"
    return (
        f"GitHub issues synced {since_str}: "
        f"{created_count} new, {updated_count} updated "
        f"({len(issues)} fetched total)."
    )


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

registry.register(Tool(
    name="sync_github_issues",
    description=(
        "Sync GitHub issues into blob storage for searchable reference. "
        "Incremental: only fetches issues updated since last sync. "
        "First run does a full fetch. Safe to call repeatedly — upserts, no duplicates."
    ),
    parameters={
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "description": "open | closed | all (default: all)",
            },
            "limit": {
                "type": "integer",
                "description": "Max issues to fetch per sync (default: 50)",
            },
        },
        "required": [],
    },
    fn=sync_github_issues,
))

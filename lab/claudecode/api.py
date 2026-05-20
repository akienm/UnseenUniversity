"""
api.py — Unified CC admin API.

Single import point for all cc-admin functions. Thin wrapper — does not
rewrite business logic, just re-exports from the canonical scripts.

Usage:
    from lab.claudecode.api import (
        list_tickets, add_ticket, done_ticket, block_ticket,
        start_session, append_change, append_decision, finalize_session, show_session,
        post_message, read_messages,
        add_decision, show_decisions, get_decision,
        write_review_findings, get_review_stats,
        sync_github, list_github,
        sync_docs,
        sync_palace,
        show_slates, render_slate,
    )

Ref: T-cc-admin-consolidation
"""

import sys
from pathlib import Path

# Ensure lab/claudecode is importable without installing as a package
_CC_DIR = Path(__file__).resolve().parent
if str(_CC_DIR) not in sys.path:
    sys.path.insert(0, str(_CC_DIR))

# ── cc_queue ──────────────────────────────────────────────────────────────────

from cc_queue import (
    cmd_list as _cq_list,
    cmd_add as _cq_add,
    cmd_done as _cq_done,
    cmd_block as block_ticket,
    cmd_show as show_ticket,
    cmd_flush_decision as flush_decision,
    cmd_flush_session as flush_session,
    _load as load_tickets,
    _save as save_tickets,
    _find as find_ticket,
)


def list_tickets(args=None):
    """Print ticket list. args: list of optional flags e.g. ['--gated']"""
    _cq_list(args or [])


def add_ticket(json_source: str):
    """Add ticket(s) from a JSON file path or inline JSON string."""
    _cq_add([json_source])


def done_ticket(ticket_id: str, result_msg: str):
    """Mark a ticket done with a result message."""
    _cq_done([ticket_id, result_msg])


# ── session_manager ───────────────────────────────────────────────────────────

from session_manager import (
    cmd_start as start_session,
    cmd_append_change as append_change,
    cmd_append_decision as append_decision,
    cmd_append_tool_output as append_tool_output,
    cmd_finalize as finalize_session,
    cmd_show as show_session,
    cmd_get as get_session,
    cmd_add as add_session,
    cmd_render as render_sessions,
    current_session_id,
)

# ── channel ───────────────────────────────────────────────────────────────────

from channel import (
    post as post_message,
    read as read_messages,
    listen as listen_channel,
    active_sessions,
    format_entry as format_channel_entry,
)

# ── decision_manager ──────────────────────────────────────────────────────────

from decision_manager import (
    cmd_add as add_decision,
    cmd_show as show_decisions,
    cmd_get as get_decision,
    cmd_resolve as resolve_decision,
    cmd_open as open_decisions,
    _update_dsb as update_dsb,
    _flush_to_igor as flush_decision_to_igor,
)

# ── review_manager ────────────────────────────────────────────────────────────

from review_manager import (
    write_findings as write_review_findings,
    get_stats as get_review_stats,
    get_check_confidence as get_review_check_confidence,
)

# ── github_sync ───────────────────────────────────────────────────────────────

from github_sync import (
    cmd_sync as sync_github,
    cmd_list as list_github,
    cmd_delta as delta_github,
    cmd_push_queue as push_queue_to_github,
)

# ── docs_sync ─────────────────────────────────────────────────────────────────

from docs_sync import (
    cmd_sync as sync_docs,
    cmd_query as query_docs,
    cmd_list as list_docs,
)

# ── palace_sync ───────────────────────────────────────────────────────────────

# palace_sync uses a main() entry point; expose it directly
import palace_sync as _palace_sync


def sync_palace(dry_run: bool = False):
    """Echo memory_palace DB → lab/theigors/ directory tree."""
    # palace_sync reads sys.argv for --dry-run; patch it temporarily
    _orig = sys.argv[:]
    try:
        sys.argv = [_orig[0]]
        if dry_run:
            sys.argv.append("--dry-run")
        _palace_sync.main()
    finally:
        sys.argv = _orig


# ── slate_manager ─────────────────────────────────────────────────────────────

from slate_manager import (
    cmd_show as show_slates,
    cmd_render as render_slate,
    cmd_add_ticket as slate_add_ticket,
    cmd_close_ticket as slate_close_ticket,
    cmd_advance as slate_advance,
)

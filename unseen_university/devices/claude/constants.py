"""
Claude device mailbox naming constants.

Naming scheme (locked 2026-04-27, D-adc-phase-0-2026-04-27):
  CC.0          — global/broadcast mailbox, always present, legacy-compatible
  CC.<session>  — per-session isolated mailbox for multi-CC deployments

Multi-CC swarms (e.g. 4-machine setup) use per-session mailboxes to avoid
cross-talk. CC.0 is the fallback broadcast when no session is specified.
"""

import os
from unseen_university._uu_root import uu_home
import socket
import subprocess
from pathlib import Path

GLOBAL_MAILBOX = "CC.0"
SESSION_MAILBOX_PREFIX = "CC."
SESSION_ID_ENV_VAR = "CLAUDE_SESSION_ID"

# ── Compaction cadence (D-compact-cadence-hook-2026-06-05) ───────────────────
# A Stop hook counts ticket-closes via sprint_tokens.log (one line per close)
# and injects /autocompact every COMPACT_EVERY_N closes. The baseline file is
# external state (shim-owned) so the count survives across CC turns/sessions.
COMPACT_EVERY_N = int(os.environ.get("CC_COMPACT_EVERY_N", "5"))


def _igor_home() -> Path:
    return Path(uu_home())


def cc_session_path() -> Path:
    """Flat file written by superclaude holding the active CC tmux session name."""
    return _igor_home() / "claudecode" / "cc_session.txt"


def _detect_session_name() -> str:
    """Return <hostname>_cc_N — lowest N with no existing tmux session.

    Underscores, not dots: tmux reserves '.' for session.window.pane targeting
    and silently stores dotted names with '.' -> '_', so a dotted scan can never
    match a real session (T-cc-tmux-session-dot-naming-broken). Must stay in sync
    with bin/superclaude's derivation and uu_bash_profile_processor.sh's default.

    For startup use only (find a free slot before the session exists).
    To find the *running* session, use _resolve_session_name() instead.
    """
    hostname = socket.gethostname().split(".")[0].lower()
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=2,
        )
        existing = set(result.stdout.splitlines())
    except Exception:
        existing = set()
    for n in range(16):
        name = f"{hostname}_cc_{n}"
        if name not in existing:
            return name
    return f"{hostname}_cc_0"


def _find_existing_cc_session() -> str | None:
    """Scan running tmux sessions for hostname_cc_N or legacy claude-main."""
    hostname = socket.gethostname().split(".")[0].lower()
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=2,
        )
        sessions = result.stdout.splitlines()
        for name in sessions:
            if name.startswith(f"{hostname}_cc_"):
                return name
        if "claude-main" in sessions:
            return "claude-main"
    except Exception:
        pass
    return None


def _resolve_session_name() -> str:
    """Return the active CC tmux session name.

    Priority (why each level exists):
      1. CC_TMUX_SESSION env var — set by superclaude, present when the hook
         subprocess inherits it from a correctly-started session.
      2. cc_session.txt flat file — written by superclaude at startup; survives
         across hook subprocess spawns even when env var is absent (e.g. session
         started before hostname-naming change shipped).
      3. tmux session scan — finds hostname_cc_N or legacy claude-main when
         neither env var nor file is available (migration safety net).
      4. _detect_session_name() slot-find — startup fallback when no session
         exists yet and we need a name to create one.
    """
    if name := os.environ.get("CC_TMUX_SESSION"):
        return name
    try:
        name = cc_session_path().read_text(encoding="utf-8").strip()
        if name:
            return name
    except (FileNotFoundError, OSError):
        pass
    if name := _find_existing_cc_session():
        return name
    return _detect_session_name()


TMUX_SESSION = _resolve_session_name()


def sprint_tokens_log_path() -> Path:
    """Path to the per-close token log — its line count is the close counter."""
    return _igor_home() / "claudecode" / "sprint_tokens.log"


def compact_baseline_path() -> Path:
    """Path to the baseline file holding the close-count at the last compaction."""
    return _igor_home() / "claudecode" / "compact_baseline.txt"


def get_session_mailbox() -> str:
    """Return CC.<session_id> if CLAUDE_SESSION_ID is set, else CC.0."""
    session_id = os.environ.get(SESSION_ID_ENV_VAR, "").strip()
    if session_id:
        return f"{SESSION_MAILBOX_PREFIX}{session_id}"
    return GLOBAL_MAILBOX

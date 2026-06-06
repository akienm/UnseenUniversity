"""
Claude device mailbox naming constants.

Naming scheme (locked 2026-04-27, D-adc-phase-0-2026-04-27):
  CC.0          — global/broadcast mailbox, always present, legacy-compatible
  CC.<session>  — per-session isolated mailbox for multi-CC deployments

Multi-CC swarms (e.g. 4-machine setup) use per-session mailboxes to avoid
cross-talk. CC.0 is the fallback broadcast when no session is specified.
"""

import os
from pathlib import Path

GLOBAL_MAILBOX = "CC.0"
SESSION_MAILBOX_PREFIX = "CC."
SESSION_ID_ENV_VAR = "CLAUDE_SESSION_ID"

# ── Compaction cadence (D-compact-cadence-hook-2026-06-05) ───────────────────
# A Stop hook counts ticket-closes via sprint_tokens.log (one line per close)
# and injects /autocompact every COMPACT_EVERY_N closes. The baseline file is
# external state (shim-owned) so the count survives across CC turns/sessions.
COMPACT_EVERY_N = int(os.environ.get("CC_COMPACT_EVERY_N", "5"))
TMUX_SESSION = os.environ.get("CC_TMUX_SESSION", "claude-main")


def _igor_home() -> Path:
    return Path(os.environ.get("IGOR_HOME", str(Path.home() / ".unseen_university")))


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

import logging

"""
Self-edit tools - Igor reads and modifies its own source code.
This is self-modification. The inertia system applies here too.

Inertia levels (mirrors memory graph):
  HIGH   (~0.95) - brainstem/, memory/models.py, cognition/reasoners/base.py
  MEDIUM (~0.75) - cognition/, memory/cortex.py
  LOW    (~0.30) - tools/, dashboard/, thalamus.py, main.py

High-inertia files require overwhelming evidence to change.
Low-inertia files are freely improvable.
Changes take effect on restart (noted in tool response).
"""

import ast
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .inertia_map import bucket_of as _im_bucket_of, weight_of as _im_weight_of
from .registry import Tool, registry
from ..cognition.forensic_logger import log_self_edit
from ..paths import paths

SOURCE_ROOT = Path(__file__).parent.parent  # wild_igor/igor/
REPO_ROOT = SOURCE_ROOT.parent  # wild_igor/

# Paths Igor may READ but never WRITE (change.26).
# Core patterns can only be changed by akien via Claude Code.
WRITE_EXCLUDED = {"brainstem/"}


_SELF_EDIT_DISABLED_MSG = (
    "Self-edit is currently disabled (IGOR_SELF_EDIT_ENABLED=false).\n"
    "Cognition stabilization is in progress — all edits are handled by Claude Code externally.\n"
    "To request this change: use create_work_order to file a GitHub work order, "
    "or contact akien directly.\n"
    "You can still READ source files with list_source_files and read_source_file."
)


def _is_self_edit_enabled() -> bool:
    """Return True if Igor is allowed to write source files."""
    val = os.getenv("IGOR_SELF_EDIT_ENABLED", "false").strip().lower()
    return val in ("true", "1", "yes")


def _log_blocked_self_edit_attempt(path: str) -> None:
    """Write a blocked-self-edit record to the instance log file."""
    log_path = paths().blocked_edits_log
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = f"{datetime.now().isoformat()}|SELF_EDIT_DISABLED|igor/{path}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/tools/self_edit.py: %s", _bare_e
        )


def _resolve(path: str) -> Path:
    resolved = (SOURCE_ROOT / path).resolve()
    if not str(resolved).startswith(str(SOURCE_ROOT.resolve())):
        raise PermissionError(f"Path '{path}' escapes Igor's source tree.")
    return resolved


def _is_write_excluded(path: str) -> bool:
    """Return True if path falls inside a WRITE_EXCLUDED directory (change.26)."""
    norm = path.replace("\\", "/").lstrip("./")
    return any(norm.startswith(excl) for excl in WRITE_EXCLUDED)


def _log_blocked_edit(path: str):
    """Append a blocked-write record to the instance log file (change.26)."""
    from datetime import datetime

    log_path = paths().blocked_edits_log
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = f"{datetime.now().isoformat()}|BLOCKED_WRITE|igor/{path}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/tools/self_edit.py: %s", _bare_e
        )


def _get_inertia(path: str) -> tuple[float, str]:
    """Return (inertia_value, label) for a given source path."""
    return _im_weight_of(path), _im_bucket_of(path)


def _path_to_module_name(rel_path: str) -> str:
    """Convert 'tools/filesystem.py' → 'wild_igor.igor.tools.filesystem'."""
    name = rel_path.replace("\\", "/").removesuffix(".py").replace("/", ".")
    return f"wild_igor.igor.{name}"


def _get_self_edit_cortex():
    """Return a Cortex instance for self-edit memory writes."""
    try:
        from ..memory.cortex import Cortex

        return Cortex(None)
    except Exception:
        return None


def _push_edit_episodic(path: str, reason: str) -> str:
    """Push an EPISODIC memory of the self-edit (D070). Returns a status line."""
    try:
        _cortex = _get_self_edit_cortex()
        if _cortex is None:
            return ""
        from ..memory.models import Memory, MemoryType

        mem = Memory(
            narrative=f"self-edited igor/{path} — {reason[:120]}",
            memory_type=MemoryType.EPISODIC,
            portable=False,
            source="self_edit",
            context_of_encoding=f"self_edit|{path}",
        )
        _cortex.store(mem)
        return f"\n📝 Episodic memory stored ({mem.id})."
    except Exception as exc:
        return f"\n⚠️  Episodic memory failed ({exc})."


def _try_hot_reload(path: str, label: str, reason: str = "") -> str:
    """
    After a successful self-edit: push EPISODIC memory (D070) then attempt hot-reload
    if gate is open and inertia is LOW. Returns a status line to append to the edit result.
    """
    memory_status = _push_edit_episodic(path, reason)

    gate = os.getenv("IGOR_HOT_RELOAD", "false").strip().lower()
    if gate not in ("true", "1", "yes"):
        return memory_status + "\n⟳ Restart Igor for changes to take effect."
    if label != "LOW":
        return (
            memory_status
            + "\n⟳ Restart Igor for changes to take effect (MEDIUM/HIGH inertia — hot-reload skipped)."
        )
    module_name = _path_to_module_name(path)
    try:
        from .hot_reload import reload_module as _reload_module

        result = _reload_module(module_name)
        log_self_edit(file=path, change_summary=f"hot_reload: {result}")
        return memory_status + f"\n⟳ Hot-reload: {result}"
    except Exception as exc:
        return (
            memory_status
            + f"\n⟳ Hot-reload failed ({exc}) — restart Igor for changes to take effect."
        )


def _inertia_warning(path: str, inertia: float, label: str) -> str:
    if label == "HIGH":
        return (
            f"\n⚠️  INERTIA WARNING: {path} has inertia {inertia:.2f} ({label}). "
            f"This is a core file. Changes here require overwhelming evidence "
            f"that they reduce friction. Proceed with extreme care."
        )
    elif label == "MEDIUM":
        return (
            f"\n⚡ INERTIA NOTICE: {path} has inertia {inertia:.2f} ({label}). "
            f"This is an architectural file. Validate carefully."
        )
    return ""


def _git_commit_and_push(rel_path: str, reason: str) -> str:
    """
    Stage the changed file, commit with a meaningful message, and push.
    Returns a status string to append to the edit result.
    """
    try:
        # Check if we're inside a git repo
        check = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            return "\n⚠️  Git: not a git repo — skipping commit/push."

        # Stage the file (path relative to repo root)
        stage_path = f"igor/{rel_path}"
        subprocess.run(
            ["git", "add", stage_path], cwd=REPO_ROOT, check=True, capture_output=True
        )

        # Commit
        commit_msg = f"self-edit: igor/{rel_path}\n\n{reason}"
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )

        # Push
        push_result = subprocess.run(
            ["git", "push"], cwd=REPO_ROOT, capture_output=True, text=True
        )
        if push_result.returncode == 0:
            return "\n✅ Git: committed and pushed to origin."
        else:
            return f"\n⚠️  Git: committed locally but push failed: {push_result.stderr.strip()}"

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        # "nothing to commit" is not really an error
        if "nothing to commit" in stderr:
            return "\n✅ Git: nothing new to commit (file unchanged on disk?)."
        return f"\n⚠️  Git error: {stderr.strip()}"
    except FileNotFoundError:
        return "\n⚠️  Git: git not found in PATH — skipping commit/push."
    except Exception as e:
        return f"\n⚠️  Git: unexpected error: {e}"


def list_source_files(path: str = ".") -> str:
    """List Igor's source files with their inertia levels."""
    try:
        target = _resolve(path)
        if not target.exists():
            return f"Error: Not found: {path}"

        lines = [f"Source tree: igor/{path}", ""]
        for f in sorted(target.rglob("*.py")):
            rel = str(f.relative_to(SOURCE_ROOT))
            inertia, label = _get_inertia(rel)
            lines.append(f"  [{label:6}] {rel}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def read_source_file(path: str) -> str:
    """Read one of Igor's own source files."""
    try:
        target = _resolve(path)
        if not target.exists():
            return f"Error: File not found: {path}"
        if not target.is_file():
            return f"Error: Not a file: {path}"

        inertia, label = _get_inertia(path)
        content = target.read_text(encoding="utf-8")
        warning = _inertia_warning(path, inertia, label)

        return f"[igor/{path}] (inertia={inertia:.2f}, {label}){warning}\n\n{content}"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error reading {path}: {e}"


def edit_source_file(path: str, content: str, reason: str) -> str:
    """
    Write new content to one of Igor's source files.
    Runs syntax check first. Records reason for the change.
    Commits and pushes to git so all Igor instances stay in sync.
    Change takes effect on next restart.
    brainstem/ is read-only for Igor — writes are blocked (change.26).
    Disabled entirely when IGOR_SELF_EDIT_ENABLED=false (WO6).
    """
    try:
        # WO6: self-edit disabled during cognition stabilization
        if not _is_self_edit_enabled():
            _log_blocked_self_edit_attempt(path)
            log_self_edit(
                file=path, blocked=True, block_reason="IGOR_SELF_EDIT_ENABLED=false"
            )
            return _SELF_EDIT_DISABLED_MSG

        # change.26: hard write exclusion for brainstem/
        if _is_write_excluded(path):
            _log_blocked_edit(path)
            log_self_edit(
                file=path, blocked=True, block_reason="write_excluded(brainstem)"
            )
            return (
                f"BLOCKED_EDIT: igor/{path} is in a write-protected directory.\n"
                f"brainstem/ contains core patterns that only akien may modify via Claude Code.\n"
                f"This attempt has been logged to blocked_edits.log."
            )

        # GitHub #69: HIGH inertia gate — files at >=0.90 require human approval via arbiter
        inertia, label = _get_inertia(path)
        if label == "HIGH":
            from ..arbiter import queue as _arbiter_queue

            arbiter_id = _arbiter_queue.submit(
                description=f"HIGH-inertia self-edit request: igor/{path}",
                context=reason,
                action_type="high_inertia_edit",
                threshold_reason=f"inertia={inertia:.2f} — gate requires human approval (GitHub #69)",
                metadata={"path": path, "inertia": inertia},
            )
            _log_blocked_edit(path)
            log_self_edit(
                file=path,
                blocked=True,
                block_reason=f"HIGH_INERTIA_GATE|arbiter#{arbiter_id}",
            )
            return (
                f"BLOCKED_HIGH_INERTIA: igor/{path} (inertia={inertia:.2f}).\n"
                f"Self-edits to HIGH-inertia files require human approval (GitHub #69).\n"
                f"Submitted to arbiter queue as item #{arbiter_id}. "
                f"Akien will review via /arbiter or the web dashboard.\n"
                f"Reason: {reason}"
            )

        target = _resolve(path)
        warning = _inertia_warning(path, inertia, label)

        # Syntax check before writing anything
        try:
            ast.parse(content)
        except SyntaxError as e:
            log_self_edit(
                file=path,
                syntax_ok=False,
                reason=reason,
                change_summary=f"SyntaxError L{e.lineno}: {e.msg}",
            )
            return (
                f"EDIT REJECTED: Syntax error in proposed change to {path}:\n"
                f"  Line {e.lineno}: {e.msg}\n"
                f"  {e.text}\n"
                f"File not modified."
            )

        # Back up original
        if target.exists():
            backup = target.with_suffix(".py.bak")
            backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")

        # Write
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        # Commit & push
        git_status = _git_commit_and_push(path, reason)

        # Extract git hash from status message
        git_hash = ""
        for part in git_status.split():
            if len(part) == 7 and all(c in "0123456789abcdef" for c in part):
                git_hash = part
                break
        log_self_edit(
            file=path,
            syntax_ok=True,
            reason=reason,
            change_summary=f"full_rewrite({len(content)} chars)",
            git_hash=git_hash,
        )

        reload_status = _try_hot_reload(path, label, reason)
        return (
            f"EDIT APPLIED: igor/{path}{warning}\n"
            f"Reason: {reason}\n"
            f"Backup: {path}.bak"
            f"{git_status}"
            f"{reload_status}"
        )

    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error editing {path}: {e}"


def patch_source_file(path: str, old_string: str, new_string: str, reason: str) -> str:
    """
    Make a targeted edit to one of Igor's source files.
    Replaces old_string with new_string — only the changed lines are sent,
    not the whole file. Safer and cheaper than edit_source_file for small changes.
    brainstem/ is read-only for Igor — writes are blocked (change.26).
    Disabled entirely when IGOR_SELF_EDIT_ENABLED=false (WO6).

    Fails clearly if:
      - old_string not found (typo or stale read)
      - old_string found more than once (ambiguous — add more context)
      - result has a syntax error (original restored)
    """
    try:
        # WO6: self-edit disabled during cognition stabilization
        if not _is_self_edit_enabled():
            _log_blocked_self_edit_attempt(path)
            log_self_edit(
                file=path, blocked=True, block_reason="IGOR_SELF_EDIT_ENABLED=false"
            )
            return _SELF_EDIT_DISABLED_MSG

        # change.26: hard write exclusion for brainstem/
        if _is_write_excluded(path):
            _log_blocked_edit(path)
            log_self_edit(
                file=path, blocked=True, block_reason="write_excluded(brainstem)"
            )
            return (
                f"BLOCKED_EDIT: igor/{path} is in a write-protected directory.\n"
                f"brainstem/ contains core patterns that only akien may modify via Claude Code.\n"
                f"This attempt has been logged to blocked_edits.log."
            )

        # GitHub #69: HIGH inertia gate — files at >=0.90 require human approval via arbiter
        inertia, label = _get_inertia(path)
        if label == "HIGH":
            from ..arbiter import queue as _arbiter_queue

            arbiter_id = _arbiter_queue.submit(
                description=f"HIGH-inertia self-edit request: igor/{path}",
                context=reason,
                action_type="high_inertia_edit",
                threshold_reason=f"inertia={inertia:.2f} — gate requires human approval (GitHub #69)",
                metadata={"path": path, "inertia": inertia},
            )
            _log_blocked_edit(path)
            log_self_edit(
                file=path,
                blocked=True,
                block_reason=f"HIGH_INERTIA_GATE|arbiter#{arbiter_id}",
            )
            return (
                f"BLOCKED_HIGH_INERTIA: igor/{path} (inertia={inertia:.2f}).\n"
                f"Self-edits to HIGH-inertia files require human approval (GitHub #69).\n"
                f"Submitted to arbiter queue as item #{arbiter_id}. "
                f"Akien will review via /arbiter or the web dashboard.\n"
                f"Reason: {reason}"
            )

        target = _resolve(path)
        if not target.exists():
            return f"PATCH REJECTED: File not found: igor/{path}"

        warning = _inertia_warning(path, inertia, label)

        original = target.read_text(encoding="utf-8")
        count = original.count(old_string)

        if count == 0:
            return (
                f"PATCH REJECTED: old_string not found in igor/{path}.\n"
                f"Re-read the file first — it may have changed since you last read it."
            )
        if count > 1:
            return (
                f"PATCH REJECTED: old_string matched {count} times in igor/{path} — ambiguous.\n"
                f"Add more surrounding context to old_string to make it unique."
            )

        patched = original.replace(old_string, new_string, 1)

        # Syntax check on result before touching disk
        try:
            ast.parse(patched)
        except SyntaxError as e:
            log_self_edit(
                file=path,
                syntax_ok=False,
                reason=reason,
                change_summary=f"patch SyntaxError L{e.lineno}: {e.msg}",
            )
            return (
                f"PATCH REJECTED: Result has a syntax error in igor/{path}:\n"
                f"  Line {e.lineno}: {e.msg}\n"
                f"  {e.text}\n"
                f"File not modified."
            )

        # Back up and write
        backup = target.with_suffix(".py.bak")
        backup.write_text(original, encoding="utf-8")
        target.write_text(patched, encoding="utf-8")

        git_status = _git_commit_and_push(path, reason)

        git_hash = ""
        for part in git_status.split():
            if len(part) == 7 and all(c in "0123456789abcdef" for c in part):
                git_hash = part
                break
        lines_changed = new_string.count("\n") - old_string.count("\n")
        sign = "+" if lines_changed >= 0 else ""
        log_self_edit(
            file=path,
            syntax_ok=True,
            reason=reason,
            change_summary=f"patch({sign}{lines_changed} lines)",
            git_hash=git_hash,
        )

        reload_status = _try_hot_reload(path, label, reason)
        return (
            f"PATCH APPLIED: igor/{path}{warning}\n"
            f"Reason: {reason}\n"
            f"Lines delta: {sign}{lines_changed} | Backup: {path}.bak"
            f"{git_status}"
            f"{reload_status}"
        )

    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error patching {path}: {e}"


def run_syntax_check(path: str) -> str:
    """Check a source file for syntax errors without running it."""
    try:
        target = _resolve(path)
        if not target.exists():
            return f"Error: File not found: {path}"

        content = target.read_text(encoding="utf-8")
        try:
            ast.parse(content)
            return f"OK: igor/{path} — no syntax errors."
        except SyntaxError as e:
            return (
                f"SYNTAX ERROR in igor/{path}:\n"
                f"  Line {e.lineno}: {e.msg}\n"
                f"  {e.text}"
            )
    except Exception as e:
        return f"Error: {e}"


# Register tools
registry.register(
    Tool(
        name="list_source_files",
        description="List Igor's own Python source files with their inertia levels (HIGH/MEDIUM/LOW). HIGH inertia files are core and should rarely change.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Subdirectory to list (default: all source files)",
                },
            },
            "required": [],
        },
        fn=list_source_files,
    )
)

registry.register(
    Tool(
        name="read_source_file",
        description="Read one of Igor's own Python source files. Use this to understand current implementation before making changes.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within igor/ source tree, e.g. 'tools/filesystem.py'",
                },
            },
            "required": ["path"],
        },
        fn=read_source_file,
    )
)

registry.register(
    Tool(
        name="edit_source_file",
        description=(
            "Write new content to one of Igor's own source files. "
            "Runs syntax check first — rejects changes that don't parse. "
            "Always provide a reason. High-inertia files (brainstem, core memory) "
            "require strong justification. Changes take effect on restart."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within igor/ source tree",
                },
                "content": {
                    "type": "string",
                    "description": "Complete new file content",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this change reduces friction or improves Igor",
                },
            },
            "required": ["path", "content", "reason"],
        },
        fn=edit_source_file,
    )
)

registry.register(
    Tool(
        name="patch_source_file",
        description=(
            "Make a targeted patch to one of Igor's source files. "
            "Replaces old_string with new_string — only the changed lines, not the whole file. "
            "PREFER THIS over edit_source_file for any change smaller than ~50 lines. "
            "Fails clearly if old_string is not found or matches multiple times (add more context). "
            "Syntax-checked before writing. Change takes effect on restart."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within igor/ source tree",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find and replace (must be unique in the file)",
                },
                "new_string": {"type": "string", "description": "Replacement text"},
                "reason": {
                    "type": "string",
                    "description": "Why this change reduces friction or improves Igor",
                },
            },
            "required": ["path", "old_string", "new_string", "reason"],
        },
        fn=patch_source_file,
    )
)

registry.register(
    Tool(
        name="run_syntax_check",
        description="Check a source file for Python syntax errors without modifying it. Use before edit_source_file to validate proposed changes.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within igor/ source tree",
                },
            },
            "required": ["path"],
        },
        fn=run_syntax_check,
    )
)

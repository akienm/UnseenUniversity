"""exec_tools.py — shell_exec MCP tool for Librarian.

Protection model: machine-level. This machine is dedicated to the Igor
project; no path or command whitelist is applied.
"""

from __future__ import annotations

import json
import subprocess


def shell_exec(
    cmd: str,
    cwd: str | None = None,
    timeout_s: float = 30.0,
) -> dict:
    """Run cmd in a shell. Returns {stdout, stderr, exit_code, cmd, cwd, timed_out}."""
    from unseen_university.action_log import append_action

    timeout_s = min(float(timeout_s), 300.0)
    timed_out = False
    stdout = ""
    stderr = ""
    exit_code: int | None = None

    try:
        proc = subprocess.run(
            cmd,
            shell=True,  # machine-level protection; dedicated Igor machine
            capture_output=True,
            text=True,
            cwd=cwd or None,
            timeout=timeout_s,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = -1

    result = {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "cmd": cmd,
        "cwd": cwd,
        "timed_out": timed_out,
    }
    append_action(
        "librarian",
        "shell_exec",
        {"cmd": cmd[:200], "cwd": cwd, "timeout_s": timeout_s},
        f"exit={exit_code} timed_out={timed_out}",
        exit_code=exit_code,
    )
    return result


# ── MCP wiring ────────────────────────────────────────────────────────────────

SCHEMAS: list[dict] = [
    {
        "name": "shell_exec",
        "description": (
            "Run a shell command on the Igor machine. "
            "Returns {stdout, stderr, exit_code, cmd, cwd, timed_out}. "
            "Machine-level protection — no path whitelist. Max timeout 300s."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Shell command to run"},
                "cwd": {
                    "type": "string",
                    "description": "Working directory (default: server CWD)",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Timeout in seconds (max 300, default 30)",
                    "default": 30,
                },
            },
            "required": ["cmd"],
        },
    }
]


def dispatch(name: str, args: dict) -> str | None:
    if name == "shell_exec":
        result = shell_exec(
            cmd=args["cmd"],
            cwd=args.get("cwd"),
            timeout_s=float(args.get("timeout_s", 30.0)),
        )
        return json.dumps(result)
    return None

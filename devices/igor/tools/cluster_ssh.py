"""
Cluster SSH tools — run commands on remote cluster machines via SSH.

Uses the igor_wild_0001 user and the keypair at ~/.TheIgors/igor_id_rsa.
Machine inventory is read from ~/.TheIgors/local/machines.json (ssh:true entries only).

Two tools registered:
  ssh_exec(machine, command)  — run a shell command on a named machine
  cluster_status()            — ping all SSH-capable machines, return Ollama health

Windows note: remote machines run Windows OpenSSH; commands are PowerShell by default.
Use PowerShell syntax for commands on Windows targets (ls, Get-Process, etc.).
For cross-platform commands, prefer: ollama list, ollama ps, ollama --version.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

from .registry import Tool, registry

# ── Config ────────────────────────────────────────────────────────────────────

_MACHINES_JSON = Path.home() / ".TheIgors" / "local" / "machines.json"
_KEY_PATH      = Path.home() / ".TheIgors" / "igor_id_rsa"
_DEFAULT_USER  = "igor_wild_0001"
_SSH_TIMEOUT   = 20   # seconds per command


def _load_machines() -> list[dict]:
    try:
        data = json.loads(_MACHINES_JSON.read_text(encoding="utf-8"))
        return data.get("machines", [])
    except Exception:
        return []


def _machine_by_name(name: str) -> dict | None:
    for m in _load_machines():
        if m["hostname"] == name or m.get("ip") == name:
            return m
    return None


def _ssh_run(ip: str, user: str, command: str, timeout: int = _SSH_TIMEOUT) -> str:
    """Run command on remote host via SSH key auth. Returns stdout+stderr."""
    key = str(_KEY_PATH)
    cmd = [
        "ssh",
        "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=8",
        f"{user}@{ip}",
        command,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0 and not out:
            return f"[exit {result.returncode}] {err}" if err else f"[exit {result.returncode}]"
        return (out + ("\n" + err if err else "")).strip()
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"
    except Exception as exc:
        return f"[ssh error: {exc}]"


# ── Tool: ssh_exec ────────────────────────────────────────────────────────────

def _ssh_exec(machine: str, command: str) -> str:
    """
    Run a command on a named cluster machine via SSH.

    machine: hostname or IP from machines.json (must have ssh:true)
    command: shell command to run (PowerShell on Windows targets)

    Returns stdout/stderr from the remote command.
    """
    m = _machine_by_name(machine)
    if m is None:
        machines = [x["hostname"] for x in _load_machines() if x.get("ssh")]
        return (f"Unknown machine '{machine}'. "
                f"SSH-capable machines: {', '.join(machines) or 'none configured yet'}")
    if not m.get("ssh"):
        return (f"Machine '{machine}' does not have SSH enabled yet. "
                f"Check machines.json — ssh:true is required.")
    ip   = m.get("ip")
    user = m.get("ssh_user", _DEFAULT_USER)
    if not ip:
        return f"Machine '{machine}' has no IP address in machines.json."
    return _ssh_run(ip, user, command)


registry.register(Tool(
    name="ssh_exec",
    description=(
        "Run a shell command on a remote cluster machine via SSH. "
        "Use machine hostname (e.g. 'akiendell') and a command string. "
        "Windows machines run PowerShell; use PowerShell syntax. "
        "Good for: checking Ollama status, running benchmarks, listing models, "
        "inspecting disk/memory on remote nodes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "machine": {
                "type": "string",
                "description": "Hostname or IP of the target machine (must be in machines.json with ssh:true)",
            },
            "command": {
                "type": "string",
                "description": "Shell command to run on the remote machine (PowerShell on Windows)",
            },
        },
        "required": ["machine", "command"],
    },
    fn=_ssh_exec,
))


# ── Tool: cluster_status ──────────────────────────────────────────────────────

def _ollama_models(ip: str, port: int = 11434, timeout: int = 5) -> list[str] | None:
    """Return list of model names from Ollama HTTP API, or None on failure."""
    try:
        url = f"http://{ip}:{port}/api/tags"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None


def _cluster_status() -> str:
    """
    Check Ollama health (HTTP) and SSH connectivity for all cluster machines.
    Returns a summary table.
    """
    machines = [m for m in _load_machines() if m.get("ip") and m.get("status") == "online"]
    if not machines:
        return "No online machines in machines.json."

    lines = ["Cluster status:\n"]
    for m in machines:
        ip   = m["ip"]
        host = m["hostname"]
        port = m.get("ollama_port", 11434)
        # Ollama health via HTTP (works regardless of SSH/PATH issues)
        models = _ollama_models(ip, port)
        if models is None:
            ollama_str = "✗ Ollama unreachable"
        else:
            ollama_str = f"✓ Ollama: {len(models)} model(s)"
        # SSH check (optional — just reports if available)
        if m.get("ssh"):
            user   = m.get("ssh_user", _DEFAULT_USER)
            result = _ssh_run(ip, user, "echo ssh_ok", timeout=8)
            ssh_str = "SSH ✓" if "ssh_ok" in result else f"SSH ✗"
        else:
            ssh_str = "SSH —"
        lines.append(f"  {host:<18} {ollama_str:<28} {ssh_str}")
    return "\n".join(lines)


# ── Tool: bootstrap_ssh ───────────────────────────────────────────────────────

def _bootstrap_ssh(machine: str = "") -> str:
    """
    Push Igor's SSH public key to machines that don't have key auth yet.
    Uses WINDOWS_USER_IGOR_USER / WINDOWS_USER_IGOR_PW from .env for
    initial password-based login, then installs the key.

    machine: specific hostname to bootstrap, or "" to try all non-SSH machines.
    """
    try:
        import paramiko
    except ImportError:
        return "paramiko not installed — run: pip install paramiko"

    igor_user = os.getenv("WINDOWS_USER_IGOR_USER", "igor_wild_0001")
    igor_pw   = os.getenv("WINDOWS_USER_IGOR_PW", "")
    pubkey    = _KEY_PATH.with_suffix(".pub").read_text().strip()

    if not igor_pw:
        return "WINDOWS_USER_IGOR_PW not set in .env"

    targets = _load_machines()
    if machine:
        targets = [m for m in targets if m["hostname"] == machine or m.get("ip") == machine]
        if not targets:
            return f"Machine '{machine}' not found in machines.json."
    else:
        targets = [m for m in targets if m.get("ip") and not m.get("ssh")
                   and m.get("status") == "online"]

    if not targets:
        return "No machines need SSH bootstrapping."

    results = []
    for m in targets:
        ip   = m["ip"]
        host = m["hostname"]
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(ip, username=igor_user, password=igor_pw, timeout=12)
            # Create .ssh dir + write authorized_keys
            ps = (
                f"New-Item -ItemType Directory -Force -Path "
                f"'C:\\\\Users\\\\{igor_user}\\\\.ssh' | Out-Null; "
                f"Set-Content -Path 'C:\\\\Users\\\\{igor_user}\\\\.ssh\\\\authorized_keys' "
                f"-Value '{pubkey}'"
            )
            _, stdout, stderr = client.exec_command(f'powershell -Command "{ps}"')
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
            client.close()
            if err and "error" in err.lower():
                results.append(f"  {host}: ✗ {err[:120]}")
            else:
                results.append(f"  {host}: ✓ key installed")
        except Exception as exc:
            results.append(f"  {host}: ✗ {exc}")

    return "SSH bootstrap results:\n" + "\n".join(results)


registry.register(Tool(
    name="bootstrap_ssh",
    description=(
        "Push Igor's SSH public key to cluster machines that don't have key auth yet. "
        "Uses WINDOWS_USER_IGOR_PW from .env for initial password login, then installs "
        "the key for future passwordless access. "
        "Optionally specify a machine name to target just one box."
    ),
    parameters={
        "type": "object",
        "properties": {
            "machine": {
                "type": "string",
                "description": "Hostname to bootstrap (leave empty to try all non-SSH machines)",
            },
        },
        "required": [],
    },
    fn=_bootstrap_ssh,
))


registry.register(Tool(
    name="cluster_status",
    description=(
        "Check SSH connectivity and Ollama health on all cluster machines. "
        "Returns a one-line status per machine showing whether SSH works and "
        "how many Ollama models are available."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    fn=_cluster_status,
))

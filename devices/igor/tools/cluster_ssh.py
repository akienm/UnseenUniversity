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
from ..paths import paths

# ── Config ────────────────────────────────────────────────────────────────────

_MACHINES_JSON = paths().machines_json
_KEY_PATH = paths().ssh_key
_DEFAULT_USER = "igor_wild_0001"
_SSH_TIMEOUT = 20  # seconds per command


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
        "-i",
        key,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=8",
        f"{user}@{ip}",
        command,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0 and not out:
            return (
                f"[exit {result.returncode}] {err}"
                if err
                else f"[exit {result.returncode}]"
            )
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
        return (
            f"Unknown machine '{machine}'. "
            f"SSH-capable machines: {', '.join(machines) or 'none configured yet'}"
        )
    if not m.get("ssh"):
        return (
            f"Machine '{machine}' does not have SSH enabled yet. "
            f"Check machines.json — ssh:true is required."
        )
    ip = m.get("ip")
    user = m.get("ssh_user", _DEFAULT_USER)
    if not ip:
        return f"Machine '{machine}' has no IP address in machines.json."
    return _ssh_run(ip, user, command)


registry.register(
    Tool(
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
    )
)


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
    machines = [
        m for m in _load_machines() if m.get("ip") and m.get("status") == "online"
    ]
    if not machines:
        return "No online machines in machines.json."

    lines = ["Cluster status:\n"]
    for m in machines:
        ip = m["ip"]
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
            user = m.get("ssh_user", _DEFAULT_USER)
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
    igor_pw = os.getenv("WINDOWS_USER_IGOR_PW", "")
    pubkey = _KEY_PATH.with_suffix(".pub").read_text().strip()

    if not igor_pw:
        return "WINDOWS_USER_IGOR_PW not set in .env"

    targets = _load_machines()
    if machine:
        targets = [
            m for m in targets if m["hostname"] == machine or m.get("ip") == machine
        ]
        if not targets:
            return f"Machine '{machine}' not found in machines.json."
    else:
        targets = [
            m
            for m in targets
            if m.get("ip") and not m.get("ssh") and m.get("status") == "online"
        ]

    if not targets:
        return "No machines need SSH bootstrapping."

    results = []
    for m in targets:
        ip = m["ip"]
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


registry.register(
    Tool(
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
    )
)


registry.register(
    Tool(
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
    )
)


# ── G40: Cluster load awareness ───────────────────────────────────────────────

# Psutil one-liner — same metrics as local _resource_load_dict()
_LOAD_CMD = (
    'python3 -c "'
    "import psutil,os,json;"
    "load1,*_=os.getloadavg();"
    "cpu=load1/(os.cpu_count() or 1)*100;"
    "vm=psutil.virtual_memory();"
    "sw=psutil.swap_memory();"
    "print(json.dumps({'cpu':round(cpu,1),'ram':round(vm.percent,1),'swap':round(sw.percent,1)}))"
    '"'
)

_LOAD_CACHE: dict[str, dict] = {}  # hostname → {verdict, cpu, ram, swap, ts}
_LOAD_CACHE_TTL_SEC = 60  # refresh at most once per minute


def get_cluster_loads(force_refresh: bool = False) -> dict[str, dict]:
    """
    G40: SSH-poll each online SSH-capable machine for CPU/RAM/swap load.
    Returns {hostname: {verdict, cpu, ram, swap}} — cached for 60s.
    verdict: "ok" | "warn" | "critical" | "unreachable"

    Thresholds (same as local IGOR_LOAD_* env vars):
      CPU warn ≥ 80%, RAM warn ≥ 80%, swap warn ≥ 40%
      CPU crit ≥ 95%, RAM crit ≥ 92%, swap crit ≥ 75%
    """
    import time as _time

    now = _time.time()

    cpu_warn = float(os.getenv("IGOR_LOAD_CPU_WARN", "80"))
    cpu_crit = float(os.getenv("IGOR_LOAD_CPU_CRIT", "95"))
    ram_warn = float(os.getenv("IGOR_LOAD_RAM_WARN", "80"))
    ram_crit = float(os.getenv("IGOR_LOAD_RAM_CRIT", "92"))
    swap_warn = float(os.getenv("IGOR_LOAD_SWAP_WARN", "40"))
    swap_crit = float(os.getenv("IGOR_LOAD_SWAP_CRIT", "75"))

    machines = [
        m
        for m in _load_machines()
        if m.get("ip") and m.get("ssh") and m.get("status") == "online"
    ]
    result = {}

    for m in machines:
        host = m["hostname"]
        cached = _LOAD_CACHE.get(host)
        if (
            not force_refresh
            and cached
            and now - cached.get("ts", 0) < _LOAD_CACHE_TTL_SEC
        ):
            result[host] = cached
            continue

        ip = m["ip"]
        user = m.get("ssh_user", _DEFAULT_USER)
        raw = _ssh_run(ip, user, _LOAD_CMD, timeout=10)

        try:
            metrics = json.loads(raw.strip())
            cpu = metrics.get("cpu", 0)
            ram = metrics.get("ram", 0)
            swap = metrics.get("swap", 0)
            if cpu >= cpu_crit or ram >= ram_crit or swap >= swap_crit:
                verdict = "critical"
            elif cpu >= cpu_warn or ram >= ram_warn or swap >= swap_warn:
                verdict = "warn"
            else:
                verdict = "ok"
            entry = {
                "verdict": verdict,
                "cpu": cpu,
                "ram": ram,
                "swap": swap,
                "ts": now,
            }
        except Exception:
            entry = {"verdict": "unreachable", "cpu": 0, "ram": 0, "swap": 0, "ts": now}

        _LOAD_CACHE[host] = entry
        result[host] = entry

    return result


def _cluster_load_report() -> str:
    """
    G40: Report CPU/RAM/swap load for all SSH-capable cluster machines.
    """
    loads = get_cluster_loads()
    if not loads:
        return "No SSH-capable online machines in machines.json."
    lines = ["Cluster load report:\n"]
    for host, d in loads.items():
        v = d["verdict"]
        indicator = {"ok": "✓", "warn": "⚠", "critical": "✗", "unreachable": "?"}.get(
            v, "?"
        )
        lines.append(
            f"  {indicator} {host:<18} verdict={v:<10}  "
            f"cpu={d['cpu']:5.1f}%  ram={d['ram']:5.1f}%  swap={d['swap']:5.1f}%"
        )
    return "\n".join(lines)


registry.register(
    Tool(
        name="cluster_load",
        description=(
            "G40: Check CPU, RAM, and swap load on all SSH-capable cluster machines. "
            "Returns verdict (ok/warn/critical/unreachable) + numeric stats per machine. "
            "Use before dispatching batch work to avoid overloading a stressed node."
        ),
        parameters={
            "type": "object",
            "properties": {
                "force_refresh": {
                    "type": "boolean",
                    "description": "Force fresh SSH poll (default: use 60s cache)",
                    "default": False,
                },
            },
            "required": [],
        },
        fn=lambda force_refresh=False, **_: _cluster_load_report(),
    )
)


# ── Tool: restart_ollama ──────────────────────────────────────────────────────


def _restart_ollama(machine: str = "") -> str:
    """
    Restart the Ollama systemd service on the specified machine (or local if empty/localhost).
    Waits 5 seconds then returns a health check result.

    machine: hostname or "" for local. Local machine uses sudo directly;
             remote machines use SSH (must have ssh:true in machines.json).
    """
    import socket
    import time as _time
    import logging as _logging

    local_host = socket.gethostname()
    is_local = machine in ("", "localhost", "127.0.0.1", local_host)

    # Check if machine is in use by a human before restarting
    target_host = local_host if is_local else machine
    try:
        from .routing_tools import in_use_now

        if in_use_now(target_host):
            _logging.getLogger("forensic").warning(
                "[restart_ollama] %s is currently in use — skipping restart",
                target_host,
            )
            return f"Skipped restart on {target_host}: machine is currently in use by a human."
    except Exception as _e:
        _logging.getLogger("forensic").warning(
            "[restart_ollama] in_use_now check failed for %s: %s",
            target_host,
            _e,
        )

    if is_local:
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "restart", "ollama.service"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                err = result.stderr.strip()
                return f"[restart failed exit {result.returncode}] {err}"
        except Exception as exc:
            return f"[restart error: {exc}]"
        _time.sleep(5)
        models = _ollama_models("127.0.0.1")
        if models is not None:
            return (
                f"Ollama restarted on {local_host}. {len(models)} model(s) available."
            )
        return f"Ollama restarted on {local_host} but health check timed out (may still be starting)."

    m = _machine_by_name(machine)
    if m is None:
        return f"Unknown machine '{machine}'."
    if not m.get("ssh"):
        return f"Machine '{machine}' has no SSH access — cannot restart remotely."
    ip = m["ip"]
    user = m.get("ssh_user", _DEFAULT_USER)
    out = _ssh_run(ip, user, "sudo systemctl restart ollama.service", timeout=30)
    if any(tag in out for tag in ("[exit", "[timeout", "[ssh error")):
        return f"Remote restart of {machine} failed: {out}"
    _time.sleep(5)
    port = m.get("ollama_port", 11434)
    models = _ollama_models(ip, port)
    if models is not None:
        return (
            f"Ollama restarted on {machine} ({ip}). {len(models)} model(s) available."
        )
    return f"Ollama restarted on {machine} ({ip}) but health check timed out (may still be starting)."


registry.register(
    Tool(
        name="restart_ollama",
        description=(
            "Restart the Ollama systemd service on a cluster machine. "
            "Leave machine empty (or omit) to restart on the local box. "
            "Waits 5 seconds then returns a health check result. "
            "Requires sudoers entry: akien ALL=(ALL) NOPASSWD: /bin/systemctl restart ollama.service"
        ),
        parameters={
            "type": "object",
            "properties": {
                "machine": {
                    "type": "string",
                    "description": "Hostname to restart (leave empty for local machine)",
                },
            },
            "required": [],
        },
        fn=_restart_ollama,
    )
)


# ── Tool: update_swarm (D204) ─────────────────────────────────────────────────

# Commands to run on each box.
# Git pull first; on success, touch restart.flag for every instance dir found.
_LINUX_UPDATE_CMD = (
    "cd ~/TheIgors && git pull --rebase origin main"
    ' && for d in ~/.TheIgors/*/; do touch "${d}restart.flag"; done'
    " && echo PULL_OK"
    " && ls ~/.TheIgors/ | wc -l"
)
_WINDOWS_UPDATE_CMD = (
    "cd C:\\automation\\local\\TheIgors;"
    " git pull --rebase origin main;"
    " if ($LASTEXITCODE -eq 0) {"
    ' Get-ChildItem "$env:USERPROFILE\\.TheIgors" -Directory'
    ' | ForEach-Object { New-Item -Force -ItemType File -Path "$($_.FullName)\\restart.flag" };'
    " Write-Output 'PULL_OK';"
    ' $count = (Get-ChildItem "$env:USERPROFILE\\.TheIgors" -Directory).Count;'
    ' Write-Output "instances=$count"'
    " } else { Write-Output 'PULL_FAILED' }"
)


def ssh_exec_all(
    windows_cmd: str,
    linux_cmd: str,
    timeout: int = 60,
) -> dict[str, str]:
    """
    D204: Run OS-typed commands on all SSH-capable online machines.
    Dispatches PowerShell on Windows machines, bash on Linux machines.
    Returns {hostname: output_string} for all attempted machines.
    Does NOT run on the local host (caller handles local separately).
    """
    import socket as _socket

    local_host = _socket.gethostname()
    machines = [
        m
        for m in _load_machines()
        if m.get("ip")
        and m.get("ssh")
        and m.get("status") == "online"
        and m["hostname"] != local_host
    ]
    results: dict[str, str] = {}
    for m in machines:
        ip = m["ip"]
        host = m["hostname"]
        user = m.get("ssh_user", _DEFAULT_USER)
        os_type = m.get("os", "linux").lower()
        cmd = windows_cmd if os_type == "windows" else linux_cmd
        results[host] = _ssh_run(ip, user, cmd, timeout=timeout)
    return results


def _update_swarm() -> str:
    """
    D204: Coordinated swarm update — git pull + restart flag on every box.

    For each SSH-capable remote box:
      - Run git pull --rebase origin main
      - On success: touch ~/.TheIgors/*/restart.flag for each instance dir
      - On failure: skip restart flags, report error

    For local box: direct subprocess, same logic.
    Restarts are fire-and-forget — the idle loop picks up restart.flag.

    Returns audit log: per-box pull result + instance count.
    """
    import socket as _socket

    lines = ["Swarm update initiated:"]

    # ── 1. Remote boxes ──────────────────────────────────────────────────────
    remote_results = ssh_exec_all(_WINDOWS_UPDATE_CMD, _LINUX_UPDATE_CMD, timeout=90)
    for host, out in remote_results.items():
        if "PULL_OK" in out:
            # Extract instance count if present
            count_match = None
            for part in out.split():
                if part.startswith("instances="):
                    count_match = part.split("=", 1)[1]
            count_str = f" ({count_match} instances)" if count_match else ""
            lines.append(f"  ✓ {host}: pulled and flagged{count_str}")
        elif "PULL_FAILED" in out:
            lines.append(f"  ✗ {host}: git pull failed — restart skipped")
        elif any(tag in out for tag in ("[exit", "[timeout", "[ssh error")):
            lines.append(f"  ? {host}: unreachable — {out[:80]}")
        else:
            lines.append(f"  ~ {host}: {out[:120]}")

    # ── 2. Local box ─────────────────────────────────────────────────────────
    local_host = _socket.gethostname()
    try:
        result = subprocess.run(
            ["bash", "-c", _LINUX_UPDATE_CMD],
            capture_output=True,
            text=True,
            timeout=90,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = (out + ("\n" + err if err else "")).strip()
        if "PULL_OK" in combined:
            lines.append(f"  ✓ {local_host} (local): pulled and flagged")
        else:
            lines.append(f"  ✗ {local_host} (local): pull failed — {combined[:120]}")
    except Exception as exc:
        lines.append(f"  ✗ {local_host} (local): error — {exc}")

    summary = "\n".join(lines)
    try:
        from ..cognition.forensic_logger import log_anomaly as _la

        _la(kind="SWARM_UPDATE", detail=summary[:400])
    except Exception:
        pass  # forensic_logger unavailable — non-fatal, swarm update still returned
    return summary


registry.register(
    Tool(
        name="update_swarm",
        description=(
            "D204: Coordinated swarm update — git pull + Igor restart on all cluster boxes. "
            "For each SSH-capable box: pull origin/main, then touch restart.flag for every "
            "Igor instance dir (glob ~/.TheIgors/*/). Local box handled directly (no SSH). "
            "Git pull failure on any box skips restart flags for that box. "
            "Returns per-box audit log."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_update_swarm,
    )
)

"""
Cluster SSH tools — run commands on remote cluster machines via SSH.

Uses the Igor-wild-0001 user and the keypair at ~/.TheIgors/igor_id_rsa.
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

from devices.igor.tools.registry import Tool, registry
from ..paths import paths

# ── Config ────────────────────────────────────────────────────────────────────

_MACHINES_JSON = paths().machines_json
_KEY_PATH = paths().ssh_key
_DEFAULT_USER = "Igor-wild-0001"
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
        "-n",  # redirect stdin from /dev/null — prevents tty blocking
        "-i",
        key,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "BatchMode=yes",  # fail immediately if interactive auth required
        f"{user}@{ip}",
        command,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
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


# Windows Ollama is installed per-user (not in SSH PATH). Use full path via PS call operator.
_WIN_OLLAMA_PATH = r"C:\Users\akien\AppData\Local\Programs\Ollama\ollama.exe"


def _win_ps_encode(ps_script: str) -> str:
    """
    Encode a PowerShell script as a base64 EncodedCommand for safe SSH transmission.
    Avoids all shell quoting issues — the encoded payload is opaque to cmd.exe.
    Windows OpenSSH default shell is cmd.exe; this ensures PowerShell is always invoked.
    """
    import base64

    encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"


def _ssh_run_machine(m: dict, command: str, timeout: int = _SSH_TIMEOUT) -> str:
    """
    OS-aware SSH runner. Wraps Windows commands via PowerShell EncodedCommand so
    callers never need to think about cmd.exe vs PowerShell quoting.
    Linux commands are passed directly (SSH → bash).
    """
    ip = m.get("ip", "")
    user = m.get("ssh_user", _DEFAULT_USER)
    os_type = m.get("os", "linux").lower()
    if os_type == "windows":
        command = _win_ps_encode(command)
    return _ssh_run(ip, user, command, timeout=timeout)


def _ollama_cmd(machine: dict, subcommand: str) -> str:
    """
    Return the shell command to run 'ollama <subcommand>' on the given machine.
    Returns PowerShell syntax for Windows (full path, single-quoted), bash for Linux.
    The command is passed through _ssh_run_machine which handles OS-level encoding.
    """
    os_type = machine.get("os", "linux").lower()
    if os_type == "windows":
        return f"& '{_WIN_OLLAMA_PATH}' {subcommand}"
    return f"ollama {subcommand}"


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
    if not m.get("ip"):
        return f"Machine '{machine}' has no IP address in machines.json."
    return _ssh_run_machine(m, command)


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


# ── Tool: run_swarm_model_sync ───────────────────────────────────────────────────


def _run_swarm_model_sync(ollama_model: str, ollama_model_batch: str = "", **_) -> str:
    """
    Pull specified Ollama model(s) on all SSH-capable machines in the cluster.

    ollama_model:       model to pull on every online machine
    ollama_model_batch: optional second model to also pull

    Returns a summary string with per-machine results.
    """
    import logging

    log = logging.getLogger(__name__)
    machines = [m for m in _load_machines() if m.get("ssh")]
    if not machines:
        return "[run_swarm_model_sync] no SSH-capable machines configured"

    lines = []
    for m in machines:
        hostname = m["hostname"]
        for model in filter(None, [ollama_model, ollama_model_batch or ""]):
            cmd = _ollama_cmd(m, f"pull {model}")
            result = _ssh_exec(hostname, cmd)
            line = f"{hostname}/{model}: {result[:120]}"
            lines.append(line)
            log.info("[run_swarm_model_sync] %s", line)

    return "\n".join(lines) if lines else "[run_swarm_model_sync] no machines reached"


registry.register(
    Tool(
        name="run_swarm_model_sync",
        description=(
            "Pull Ollama model(s) on every SSH-capable machine in the cluster. "
            "Calls 'ollama pull <model>' via ssh_exec on each online machine. "
            "Use when a new model needs to be distributed to all nodes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ollama_model": {
                    "type": "string",
                    "description": "Primary Ollama model to pull on all machines",
                },
                "ollama_model_batch": {
                    "type": "string",
                    "description": "Optional second model to also pull",
                },
            },
            "required": ["ollama_model"],
        },
        fn=_run_swarm_model_sync,
    )
)


# ── Tool: cluster_status ──────────────────────────────────────────────────────


def _ping(ip: str) -> bool:
    """ICMP ping — returns True if box responds, False if unreachable. Near-instant (2s max)."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", ip],
            capture_output=True,
            timeout=4,
        )
        return result.returncode == 0
    except Exception:
        return False


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
        # Ping first — fast reachability check
        alive = _ping(ip)
        ping_str = "PING ✓" if alive else "PING ✗"
        if not alive:
            lines.append(f"  {host:<18} {ping_str}  (unreachable)")
            continue
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
            ssh_str = "SSH ✓" if "ssh_ok" in result else "SSH ✗"
        else:
            ssh_str = "SSH —"
        lines.append(f"  {host:<18} {ping_str}  {ollama_str:<28} {ssh_str}")
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

    igor_user = os.getenv("WINDOWS_USER_IGOR_USER", "Igor-wild-0001")
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

# Psutil one-liner — Linux: uses os.getloadavg() for 1-min load average
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

# Windows: use PowerShell WMI — no Python PATH dependency.
# ConvertTo-Json -Compress avoids quote-escaping issues through SSH.
_WINDOWS_LOAD_CMD = (
    "$cpu=[math]::Round((Get-CimInstance Win32_Processor"
    " | Measure-Object LoadPercentage -Average).Average,1);"
    " $os=Get-CimInstance Win32_OperatingSystem;"
    " $ram=[math]::Round((1-$os.FreePhysicalMemory/$os.TotalVisibleMemorySize)*100,1);"
    " [PSCustomObject]@{cpu=$cpu;ram=$ram;swap=0} | ConvertTo-Json -Compress"
)

_LOAD_CACHE: dict[str, dict] = {}  # hostname → {verdict, cpu, ram, swap, ts}
_LOAD_CACHE_TTL_SEC = 60  # refresh at most once per minute


def get_cluster_loads(force_refresh: bool = False) -> dict[str, dict]:
    """
    G40: SSH-poll each online SSH-capable machine for CPU/RAM/swap load.
    Returns {hostname: {verdict, cpu, ram, swap}} — cached for 60s.
    verdict: "ok" | "warn" | "critical" | "unreachable"

    Checks run in parallel (ThreadPoolExecutor) — max wall time = one machine's timeout.
    Timeout raised to 20s to tolerate loaded Windows boxes (WMI can be slow under load).

    Thresholds (same as local IGOR_LOAD_* env vars):
      CPU warn ≥ 80%, RAM warn ≥ 80%, swap warn ≥ 40%
      CPU crit ≥ 95%, RAM crit ≥ 92%, swap crit ≥ 75%
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed

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
    to_check: list[dict] = []

    for m in machines:
        host = m["hostname"]
        cached = _LOAD_CACHE.get(host)
        if (
            not force_refresh
            and cached
            and now - cached.get("ts", 0) < _LOAD_CACHE_TTL_SEC
        ):
            result[host] = cached
        else:
            to_check.append(m)

    if not to_check:
        return result

    def _check_one(m: dict) -> tuple[str, dict]:
        host = m["hostname"]
        ip = m["ip"]
        user = m.get("ssh_user", _DEFAULT_USER)

        if not _ping(ip):
            return host, {
                "verdict": "unreachable",
                "cpu": 0,
                "ram": 0,
                "swap": 0,
                "ts": now,
                "ping": False,
            }

        os_type = m.get("os", "linux").lower()
        load_cmd = _WINDOWS_LOAD_CMD if os_type == "windows" else _LOAD_CMD
        raw = _ssh_run_machine(m, load_cmd, timeout=20)

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
            return host, {
                "verdict": verdict,
                "cpu": cpu,
                "ram": ram,
                "swap": swap,
                "ts": now,
                "ping": True,
            }
        except Exception:
            # Ping ok but SSH load check failed — box is up but SSH busy (MaxSessions on Windows
            # can be exhausted by the running Igor process).  Mark "warn" so the swarm update
            # still attempts a git pull instead of silently skipping the box.
            return host, {
                "verdict": "warn",
                "cpu": 0,
                "ram": 0,
                "swap": 0,
                "ts": now,
                "ping": True,
                "ssh_load_failed": True,
            }

    with ThreadPoolExecutor(max_workers=len(to_check)) as pool:
        futures = {pool.submit(_check_one, m): m for m in to_check}
        for fut in as_completed(futures, timeout=25):
            try:
                host, entry = fut.result()
            except Exception:
                host = futures[fut]["hostname"]
                entry = {
                    "verdict": "unreachable",
                    "cpu": 0,
                    "ram": 0,
                    "swap": 0,
                    "ts": now,
                    "ping": False,
                }
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
    os_type = m.get("os", "linux").lower()
    if os_type == "windows":
        restart_cmd = "Stop-Process -Name ollama -Force -ErrorAction SilentlyContinue; Start-Sleep 2; Start-Process -WindowStyle Hidden -FilePath 'C:\\Users\\akien\\AppData\\Local\\Programs\\Ollama\\ollama.exe'"
    else:
        restart_cmd = "sudo systemctl restart ollama.service"
    out = _ssh_run_machine(m, restart_cmd, timeout=30)
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


# ── Tool: ollama_list ────────────────────────────────────────────────────────


def _ollama_list(machine: str = "") -> str:
    """
    List Ollama models on one machine or all cluster machines.
    Uses the Ollama HTTP API — no SSH or PATH issues.

    machine: hostname to query, or "" for all online machines.
    """
    targets = [
        m for m in _load_machines() if m.get("ip") and m.get("status") == "online"
    ]
    if machine:
        targets = [
            m for m in targets if m["hostname"] == machine or m.get("ip") == machine
        ]
        if not targets:
            return f"Unknown machine '{machine}'."

    lines = []
    for m in targets:
        host = m["hostname"]
        ip = m["ip"]
        port = m.get("ollama_port", 11434)
        models = _ollama_models(ip, port)
        if models is None:
            lines.append(f"  {host}: Ollama unreachable (is it running?)")
        elif not models:
            lines.append(f"  {host}: no models installed")
        else:
            lines.append(f"  {host}: {', '.join(models)}")
    return "Ollama models:\n" + "\n".join(lines) if lines else "No machines found."


registry.register(
    Tool(
        name="ollama_list",
        description=(
            "List Ollama models installed on one or all cluster machines. "
            "Uses HTTP API — works on Linux and Windows without SSH PATH issues. "
            "Leave machine empty to list all online machines."
        ),
        parameters={
            "type": "object",
            "properties": {
                "machine": {
                    "type": "string",
                    "description": "Hostname to query (leave empty for all machines)",
                },
            },
            "required": [],
        },
        fn=lambda machine="", **_: _ollama_list(machine),
    )
)


# ── Tool: ollama_pull ────────────────────────────────────────────────────────


def _ollama_pull(model: str, machine: str = "") -> str:
    """
    Pull an Ollama model on one machine or all SSH-capable machines.

    model:   Ollama model name (e.g. 'qwen2.5:7b', 'deepseek-r1:7b')
    machine: hostname to target, or "" for all SSH-capable online machines.
    """
    if machine:
        m = _machine_by_name(machine)
        if m is None:
            return f"Unknown machine '{machine}'."
        if not m.get("ssh"):
            return f"Machine '{machine}' has no SSH access."
        if not m.get("ip"):
            return f"Machine '{machine}' has no IP in machines.json."
        cmd = _ollama_cmd(m, f"pull {model}")
        result = _ssh_run_machine(m, cmd, timeout=600)
        return f"{machine}: {result[:200]}"

    # All SSH-capable online machines
    machines = [
        m
        for m in _load_machines()
        if m.get("ssh") and m.get("ip") and m.get("status") == "online"
    ]
    if not machines:
        return "No SSH-capable online machines configured."
    lines = []
    for m in machines:
        host = m["hostname"]
        cmd = _ollama_cmd(m, f"pull {model}")
        result = _ssh_run_machine(m, cmd, timeout=600)
        lines.append(f"  {host}: {result[:200]}")
    return f"ollama pull {model}:\n" + "\n".join(lines)


registry.register(
    Tool(
        name="ollama_pull",
        description=(
            "Pull an Ollama model on one cluster machine or all SSH-capable machines. "
            "Handles Windows and Linux path differences automatically. "
            "Leave machine empty to pull on all online SSH-capable machines."
        ),
        parameters={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Ollama model name to pull (e.g. 'qwen2.5:7b')",
                },
                "machine": {
                    "type": "string",
                    "description": "Target hostname (leave empty for all machines)",
                },
            },
            "required": ["model"],
        },
        fn=lambda model, machine="", **_: _ollama_pull(model, machine),
    )
)


# ── Tool: set_powershell_default ─────────────────────────────────────────────


def _set_powershell_default(machine: str = "") -> str:
    """
    Set PowerShell as the default SSH shell on Windows machines via registry.
    Requires the SSH user to be an administrator (Igor-wild-0001 is admin).
    Prefers PowerShell 7 (pwsh.exe) if installed, falls back to PS 5.1.

    This is a one-time setup per machine. After this, SSH sessions open
    directly in PowerShell and the _win_ps_encode wrapper is still used
    (belt-and-suspenders — it never hurts to encode).

    machine: hostname to configure, or "" for all Windows online SSH machines.
    """
    _PS7_PATH = r"C:\Program Files\PowerShell\7\pwsh.exe"
    _PS5_PATH = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    _REG_CMD = (
        f"$ps7 = '{_PS7_PATH}';"
        f" $ps5 = '{_PS5_PATH}';"
        " $shell = if (Test-Path $ps7) { $ps7 } else { $ps5 };"
        " New-Item -Path 'HKLM:\\SOFTWARE\\OpenSSH' -Force | Out-Null;"
        " New-ItemProperty -Path 'HKLM:\\SOFTWARE\\OpenSSH' -Name 'DefaultShell'"
        " -Value $shell -PropertyType String -Force | Out-Null;"
        ' Write-Output "DefaultShell set to: $shell"'
    )

    targets = [
        m
        for m in _load_machines()
        if m.get("os", "linux").lower() == "windows"
        and m.get("ssh")
        and m.get("ip")
        and m.get("status") == "online"
    ]
    if machine:
        targets = [
            m for m in targets if m["hostname"] == machine or m.get("ip") == machine
        ]
        if not targets:
            return f"Machine '{machine}' not found or not a Windows SSH machine."

    if not targets:
        return "No online Windows SSH machines to configure."

    lines = []
    for m in targets:
        host = m["hostname"]
        result = _ssh_run_machine(m, _REG_CMD, timeout=20)
        lines.append(f"  {host}: {result[:200]}")
    return "set_powershell_default results:\n" + "\n".join(lines)


registry.register(
    Tool(
        name="set_powershell_default",
        description=(
            "Set PowerShell as the default SSH shell on Windows cluster machines via registry. "
            "Requires admin rights (Igor-wild-0001 is admin). "
            "Prefers PowerShell 7 (pwsh.exe), falls back to PS 5.1. "
            "One-time setup per machine — makes all future SSH sessions cleaner. "
            "Leave machine empty to configure all online Windows SSH machines."
        ),
        parameters={
            "type": "object",
            "properties": {
                "machine": {
                    "type": "string",
                    "description": "Hostname to configure (leave empty for all Windows machines)",
                },
            },
            "required": [],
        },
        fn=lambda machine="", **_: _set_powershell_default(machine),
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
    " git stash;"
    " git pull --rebase origin main;"
    " if ($LASTEXITCODE -eq 0) {"
    " git stash pop;"
    " $dirs = Get-ChildItem C:\\Users -Directory |"
    " ForEach-Object { Join-Path $_.FullName '.TheIgors' } |"
    " Where-Object { Test-Path $_ } |"
    " ForEach-Object { Get-ChildItem $_ -Directory };"
    " $dirs | ForEach-Object { New-Item -Force -ItemType File -Path (Join-Path $_.FullName 'restart.flag') | Out-Null };"
    " Write-Output 'PULL_OK';"
    ' Write-Output "instances=$($dirs.Count)"'
    " } else { Write-Output 'PULL_FAILED' }"
)


def ssh_exec_all(
    windows_cmd: str,
    linux_cmd: str,
    timeout: int = 20,
    machines: list[dict] | None = None,
) -> dict[str, str]:
    """
    D204: Run OS-typed commands on all SSH-capable online machines.
    Dispatches PowerShell on Windows machines, bash on Linux machines.
    Returns {hostname: output_string} for all attempted machines.
    Does NOT run on the local host (caller handles local separately).

    Runs machines in parallel (ThreadPoolExecutor, max 3 workers).
    Pass a pre-filtered `machines` list to skip certain hosts (e.g. overloaded boxes).
    """
    import socket as _socket
    from concurrent.futures import ThreadPoolExecutor, as_completed

    local_host = _socket.gethostname()
    if machines is None:
        machines = [
            m
            for m in _load_machines()
            if m.get("ip")
            and m.get("ssh")
            and m.get("status") == "online"
            and m["hostname"] != local_host
        ]

    def _run_one(m: dict) -> tuple[str, str]:
        ip = m["ip"]
        host = m["hostname"]
        user = m.get("ssh_user", _DEFAULT_USER)
        os_type = m.get("os", "linux").lower()
        cmd = windows_cmd if os_type == "windows" else linux_cmd
        return host, _ssh_run_machine(m, cmd, timeout=timeout)

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_run_one, m): m["hostname"] for m in machines}
        for fut in as_completed(futures):
            host, out = fut.result()
            results[host] = out
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

    # ── 1. Remote boxes — ping-gate then parallel SSH git pull ───────────────
    # Skips the SSH load-check pre-screen: that spawns SSH subprocesses which
    # can linger after timeout and exhaust Windows MaxSessions before the git
    # pull attempts. Ping is cheap, fast, and sufficient for "box is up" check.
    all_remote = [
        m
        for m in _load_machines()
        if m.get("ip")
        and m.get("ssh")
        and m.get("status") == "online"
        and m["hostname"] != _socket.gethostname()
    ]
    eligible: list[dict] = []
    for m in all_remote:
        host = m["hostname"]
        if _ping(m["ip"]):
            eligible.append(m)
        else:
            lines.append(f"  ~ {host}: skipped (ping failed — box appears down)")

    remote_results = ssh_exec_all(
        _WINDOWS_UPDATE_CMD, _LINUX_UPDATE_CMD, timeout=30, machines=eligible
    )
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
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le
        _le(kind="SILENT_EXCEPT", detail=f"cluster_ssh.py:1078: {_exc}")
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

# ── Tool: stop_swarm (T-stop-swarm-engram) ────────────────────────────────────

_LINUX_STOP_CMD = (
    'for d in ~/.TheIgors/*/; do touch "${d}exit.flag"; done'
    " && echo STOP_OK"
    " && ls ~/.TheIgors/ | wc -l"
)
_WINDOWS_STOP_CMD = (
    "$dirs = Get-ChildItem C:\\Users -Directory |"
    " ForEach-Object { Join-Path $_.FullName '.TheIgors' } |"
    " Where-Object { Test-Path $_ } |"
    " ForEach-Object { Get-ChildItem $_ -Directory };"
    " $dirs | ForEach-Object { New-Item -Force -ItemType File -Path (Join-Path $_.FullName 'exit.flag') | Out-Null };"
    " Write-Output 'STOP_OK';"
    ' Write-Output "instances=$($dirs.Count)"'
)


def stop_swarm(**_) -> str:
    """
    T-stop-swarm-engram: Gracefully stop all Igor instances across the swarm.

    Drops exit.flag in every instance dir (~/.TheIgors/*/) on each online box.
    The main loop checks exit.flag at each idle cycle and exits with code 0.
    Code 0 = the bash wrapper does NOT restart (unlike code 42 / restart.flag).

    Analogous to update_swarm but for maintenance windows, not upgrades.
    Returns per-box audit log.
    """
    import socket as _socket

    lines = ["Swarm stop initiated (exit.flag on all instances):"]

    all_remote = [
        m
        for m in _load_machines()
        if m.get("ip")
        and m.get("ssh")
        and m.get("status") == "online"
        and m["hostname"] != _socket.gethostname()
    ]
    eligible: list[dict] = []
    for m in all_remote:
        if _ping(m["ip"]):
            eligible.append(m)
        else:
            lines.append(
                f"  ~ {m['hostname']}: skipped (ping failed — box appears down)"
            )

    remote_results = ssh_exec_all(
        _WINDOWS_STOP_CMD, _LINUX_STOP_CMD, timeout=15, machines=eligible
    )
    for host, out in remote_results.items():
        if "STOP_OK" in out:
            count_match = None
            for part in out.split():
                if part.startswith("instances="):
                    count_match = part.split("=", 1)[1]
            count_str = f" ({count_match} instances)" if count_match else ""
            lines.append(f"  ✓ {host}: exit.flag dropped{count_str}")
        elif any(tag in out for tag in ("[exit", "[timeout", "[ssh error")):
            lines.append(f"  ? {host}: unreachable — {out[:80]}")
        else:
            lines.append(f"  ~ {host}: {out[:120]}")

    # Local box — drop exit.flag for all local instance dirs
    local_host = _socket.gethostname()
    try:
        result = subprocess.run(
            ["bash", "-c", _LINUX_STOP_CMD],
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = (out + ("\n" + err if err else "")).strip()
        if "STOP_OK" in combined:
            lines.append(f"  ✓ {local_host} (local): exit.flag dropped")
        else:
            lines.append(f"  ✗ {local_host} (local): error — {combined[:120]}")
    except Exception as exc:
        lines.append(f"  ✗ {local_host} (local): error — {exc}")

    summary = "\n".join(lines)
    try:
        from ..cognition.forensic_logger import log_anomaly as _la

        _la(kind="SWARM_STOP", detail=summary[:400])
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le
        _le(kind="SILENT_EXCEPT", detail=f"cluster_ssh.py:1192: {_exc}")
    return summary


registry.register(
    Tool(
        name="stop_swarm",
        description=(
            "T-stop-swarm-engram: Gracefully stop all Igor instances across the swarm. "
            "Drops exit.flag in ~/.TheIgors/*/ on every online box via SSH. "
            "exit.flag causes Igor to exit with code 0 (no restart). "
            "Use for maintenance windows. Analogous to update_swarm."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=stop_swarm,
    )
)


# ── Tool: ssh_check ──────────────────────────────────────────────────────────


def _ssh_check(**_) -> str:
    """
    Verify SSH connectivity to all SSH-capable machines in machines.json.
    Runs 'echo OK' (or 'Write-Host OK' on Windows) on each and reports results.
    """
    import time

    machines = [m for m in _load_machines() if m.get("ssh") and m.get("ip")]
    if not machines:
        return "[ssh_check] no SSH-capable machines with IPs configured"

    lines = [f"SSH check at {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    ok_count = 0
    for m in machines:
        hostname = m["hostname"]
        os_type = m.get("os", "linux").lower()
        cmd = "Write-Host OK" if os_type == "windows" else "echo OK"
        result = _ssh_run_machine(m, cmd, timeout=10)
        status = "OK" if result.strip() == "OK" else f"FAIL ({result[:80]})"
        if status == "OK":
            ok_count += 1
        user = m.get("ssh_user", _DEFAULT_USER)
        lines.append(f"  {hostname:20s} {m['ip']:15s} user={user:20s} {status}")

    lines.append(f"\n{ok_count}/{len(machines)} machines reachable")
    return "\n".join(lines)


registry.register(
    Tool(
        name="ssh_check",
        description=(
            "Verify SSH connectivity to all cluster machines. "
            "Reads machine list from machines.json (ssh:true entries), "
            "runs a simple echo command on each, reports pass/fail. "
            "Use for health checks, after network changes, or to verify cluster state."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_ssh_check,
    )
)

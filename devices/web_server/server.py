#!/usr/bin/env python3
#!/usr/bin/env python3
"""
ADC Web Server — shared agent platform layer.

Standalone Starlette/uvicorn server that runs independently of any agent.
Agents (Igor, future copilot, etc.) register as clients and push data.
Claude Code, web browsers, and other tools connect as consumers.

Endpoints (platform — always available):
  GET  /                      → serve web UI (fallback HTML if not built)
  GET  /assets/{path}         → serve web_ui/dist/assets/
  WS   /ws                    → WebSocket hub (chat, dashboard, activity)
  POST /api/cc_send           → inject message into channel (author: "claude-code")
  POST /api/upload            → save file to inbox
  GET  /api/outbox            → list outbox files
  GET  /api/outbox/{file}     → download from outbox
  GET  /api/sessions          → list active WebSocket sessions
  GET  /health                → platform health + PID + attached agents
  GET  /metrics               → platform metrics

Endpoints (agent — available when agent is registered):
  POST /api/agents/register   → agent announces itself
  POST /api/agents/deregister → agent disconnects
  POST /api/agents/{id}/stats → agent pushes dashboard data
  GET  /api/dashboard         → returns last-pushed stats from attached agent
  *    /api/agent/{id}/*      → proxied to agent's callback URL (future)

Lifecycle:
  - PID file at ADC_RUNTIME_ROOT/adc_web.pid
  - /health responds within 5s or considered stalled
  - Launchers (superclaude, igor) start this if not running
  - Second instance detects running/stalled via PID + health check

Port: ADC_WEB_PORT env var (falls back to IGOR_UC_PORT), default 8080.
"""

import asyncio
import json
import logging
import os
import platform
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_IS_WINDOWS = platform.system() == "Windows"


def _process_exists(pid: int) -> bool:
    """Cross-platform PID existence check. Never kills, never raises."""
    if pid <= 0:
        return False
    if _IS_WINDOWS:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        # Process may be a zombie — check exit code
        exit_code = ctypes.c_ulong(0)
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        STILL_ACTIVE = 259
        return exit_code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_process(pid: int) -> None:
    """Cross-platform process kill. Best-effort, never raises."""
    if pid <= 0:
        return
    if _IS_WINDOWS:
        import ctypes

        PROCESS_TERMINATE = 0x0001
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
        return
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        if _process_exists(pid):
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


import contextlib

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ── Paths ────────────────────────────────────────────────────────────────────

# ADC_RUNTIME_ROOT preferred; fall back to IGOR_RUNTIME_ROOT for backwards compat.
_RUNTIME_ROOT = Path(
    os.environ.get("ADC_RUNTIME_ROOT")
    or os.environ.get("IGOR_RUNTIME_ROOT")
    or Path.home() / ".unseen_university"
)
_INSTANCE_DIR = _RUNTIME_ROOT / os.environ.get("IGOR_INSTANCE_ID", "Igor-wild-0001")
# Web UI dist: env var override, or default to UU sibling (dev layout).
_DIST_DIR = Path(
    os.environ.get("ADC_WEB_UI_DIST")
    or Path(__file__).resolve().parents[2] / "wild_igor" / "web_ui" / "dist"
)

# ── Logging ──────────────────────────────────────────────────────────────────

_LOG_DIR = _RUNTIME_ROOT / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_file = _LOG_DIR / "web_server.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(_log_file)),
        logging.StreamHandler(),
    ],
)
log = get_logger("web_server")

INBOX_DIR = _INSTANCE_DIR / "inbox"
OUTBOX_DIR = _INSTANCE_DIR / "outbox"
PID_FILE = _RUNTIME_ROOT / "web_server.pid"

_CHANNEL_DIR = _RUNTIME_ROOT / "local" / "cc_channel"
_CHANNEL_FILE = _CHANNEL_DIR / "messages.jsonl"
_AGENT_REGISTRY_FILE = _RUNTIME_ROOT / "agent_registry.json"

# ── Boot timestamp ───────────────────────────────────────────────────────────

_boot_ts: float = time.monotonic()
_boot_wall: str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
_last_input_ts: float = 0.0

# ── Agent registry ───────────────────────────────────────────────────────────
# Agents register on boot, deregister on shutdown. Thread-safe via lock.

_agents: dict = (
    {}
)  # agent_id → {registered_at, capabilities, last_stats, last_heartbeat}
_agents_lock = threading.Lock()
_agent_stats: dict = {}  # agent_id → last stats dict pushed by agent

# ── Comms module ─────────────────────────────────────────────────────────────
# Comms transport not yet migrated to UU — channel panel disabled.
_comms = None  # reserved; set once comms migrates to UU


def _init_comms():
    log.info("Comms: not yet migrated to UU — channel panel disabled")
    return


# ── WebSocket session management ─────────────────────────────────────────────

_session_clients: dict = {}  # session_id → [asyncio.Queue, ...]
_client_session: dict = {}  # id(ws) → session_id
_session_history: dict = {}  # session_id → [{...}, ...] (capped at 50)
_client_lock = threading.Lock()
_ds_chat_history: list = []  # DickSimnel chat log (capped at 100)
_ds_chat_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None

# ── Thread-safe queue: web messages → attached agent ─────────────────────────
import queue

incoming: queue.Queue = queue.Queue()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_dirs():
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    _CHANNEL_DIR.mkdir(parents=True, exist_ok=True)


def _save_agents() -> None:
    """Persist agent registrations to disk so they survive server restarts."""
    with _agents_lock:
        # Exclude last_heartbeat — it's a monotonic value meaningless across reboots
        snapshot = {
            aid: {k: v for k, v in info.items() if k != "last_heartbeat"}
            for aid, info in _agents.items()
        }
    try:
        _AGENT_REGISTRY_FILE.write_text(
            json.dumps(snapshot, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        log.warning("agent registry save failed: %s", exc)


def _load_agents() -> None:
    """Load persisted agent registrations from disk at server startup."""
    try:
        data = json.loads(_AGENT_REGISTRY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        with _agents_lock:
            for aid, info in data.items():
                if isinstance(info, dict):
                    _agents[aid] = {**info, "last_heartbeat": None}
        log.info("Restored %d agent registration(s) from disk", len(data))
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning("agent registry load failed: %s", exc)


def _bootstrap_mkcert() -> tuple[str, str] | None:
    """Generate a locally-trusted cert via mkcert if available.

    Returns (cert_path, key_path) on success, None if mkcert isn't installed
    or generation failed. Idempotent — reuses existing files.
    """
    import shutil
    import subprocess
    from pathlib import Path

    cert_dir = _RUNTIME_ROOT / "certs"
    cert_path = cert_dir / "localhost+3.pem"
    key_path = cert_dir / "localhost+3-key.pem"

    if cert_path.exists() and key_path.exists():
        return (str(cert_path), str(key_path))

    if not shutil.which("mkcert"):
        return None

    cert_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "mkcert",
                "-cert-file",
                str(cert_path),
                "-key-file",
                str(key_path),
                "localhost",
                "127.0.0.1",
                "::1",
            ],
            cwd=str(cert_dir),
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("mkcert generation failed: %s", e)
        return None

    return (str(cert_path), str(key_path))


def _deliver_to_tmux(content: str, sender: str, channel: str) -> None:
    """Forward a message to agent tmux sessions matching the channel. Never raises."""
    try:
        from devices.claude.tmux_face import send_to_session
    except ImportError:
        return

    with _agents_lock:
        agents_snapshot = dict(_agents)

    targets: list[tuple[str, str]] = []  # (tmux_target, agent_id)
    if channel.startswith("comms://"):
        ch_name = channel[len("comms://") :]
        if ch_name == "shared":
            for agent_id, info in agents_snapshot.items():
                if info.get("tmux_target"):
                    targets.append((info["tmux_target"], agent_id))
        elif ch_name in agents_snapshot and agents_snapshot[ch_name].get("tmux_target"):
            targets.append((agents_snapshot[ch_name]["tmux_target"], ch_name))

    for tmux_target, agent_id in targets:
        try:
            send_to_session(target=tmux_target, sender=sender, message=content)
        except Exception as exc:
            log.warning(
                "tmux_deliver: agent=%s target=%s: %s", agent_id, tmux_target, exc
            )


def _channel_append(author: str, content: str, msg_type: str = "message"):
    """Mirror a message to the shared JSONL channel and Postgres. Never raises."""
    try:
        _CHANNEL_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {"ts": ts, "author": author, "type": msg_type, "content": content}
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(_CHANNEL_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        # Mirror to Postgres channel_messages so MCP channel_read sees messages
        _pg_url = os.environ.get("IGOR_HOME_DB_URL", "") or os.environ.get(
            "IGOR_DB_URL", ""
        )
        if _pg_url:
            try:
                import psycopg2

                conn = psycopg2.connect(_pg_url)
                with conn:
                    with conn.cursor() as c:
                        c.execute(
                            "INSERT INTO channel_messages"
                            " (ts, author, type, content, channel, source_agent)"
                            " VALUES (%s, %s, %s, %s, %s, %s)",
                            (ts, author, msg_type, content, "shared", author),
                        )
                conn.close()
            except Exception as pg_e:
                log.debug("channel_append PG write failed (non-fatal): %s", pg_e)
    except Exception as e:
        log.warning("channel_append error: %s", e)


def _add_to_history(session_id: str, msg: dict):
    """Add a message to session history (capped at 50)."""
    with _client_lock:
        hist = _session_history.setdefault(session_id, [])
        hist.append(msg)
        if len(hist) > 50:
            hist.pop(0)


def _broadcast_to_session(session_id: str, payload: str):
    """Fan out a payload to clients in a specific session.

    Logs fanout count for every call. If fanout_count=0, also posts a channel
    diagnostic so silent drops (agent POST 200 OK but no WS subscriber on the
    target session_id) surface in real time instead of vanishing.
    T-uc-delivery-telemetry: the suspected smoking gun is session_id mismatch
    between Igor's send default ('shared') and the browser's joined channel.
    """
    if _loop is None:
        log.warning(
            "uc_deliver: no event loop, cannot broadcast session=%s", session_id
        )
        return
    with _client_lock:
        queues = list(_session_clients.get(session_id, []))
        known_sessions = list(_session_clients.keys())

    fanout_count = len(queues)
    preview = ""
    try:
        preview = json.loads(payload).get("content", "")[:80].replace("\n", " ")
    except Exception:
        preview = payload[:80].replace("\n", " ")

    if fanout_count == 0:
        log.warning(
            "uc_deliver: DROP session=%s fanout=0 known_sessions=%s: %s",
            session_id,
            known_sessions,
            preview,
        )
        try:
            _channel_append(
                "uc_deliver",
                f"[uc_deliver] ✗ session={session_id} fanout=0 "
                f"known={known_sessions}: {preview}",
                msg_type="diagnostic",
            )
        except Exception as chexc:
            log.debug("uc_deliver: channel diagnostic failed: %s", chexc)
    else:
        log.info(
            "uc_deliver: session=%s fanout=%d: %s",
            session_id,
            fanout_count,
            preview,
        )

    for q in queues:
        try:
            _loop.call_soon_threadsafe(q.put_nowait, payload)
        except Exception as e:
            log.warning("uc_deliver: enqueue failed session=%s: %s", session_id, e)


def _broadcast(payload: str):
    """Fan out a JSON payload to every connected WebSocket client (all sessions)."""
    if _loop is None:
        return
    with _client_lock:
        all_queues = [q for qs in _session_clients.values() for q in qs]
    for q in all_queues:
        _loop.call_soon_threadsafe(q.put_nowait, payload)


# ── Public send API (called by agents via REST) ─────────────────────────────


def _canonical_session_id(sid: str) -> str:
    # Browser tabs + comms.py channel registry use the comms:// URI form;
    # bare names like "shared" are an agent-side convenience. Coerce here
    # so _session_clients/_session_history keys match the browser's join.
    if not sid:
        return "comms://shared"
    return sid if sid.startswith("comms://") else f"comms://{sid}"


def agent_send(text: str, agent_id: str, session_id: str = "shared"):
    """An agent sends a response to the web UI."""
    session_id = _canonical_session_id(session_id)
    log.info(
        "uc_deliver: agent_send agent=%s session=%s len=%d: %s",
        agent_id,
        session_id,
        len(text),
        text[:80].replace("\n", " "),
    )
    msg = {
        "type": "message",
        "author": agent_id,
        "content": text,
        "ts": _ts(),
        "session_id": session_id,
    }
    _add_to_history(session_id, msg)
    _broadcast_to_session(session_id, json.dumps(msg))
    _channel_append(agent_id, text)


# ── Route handlers ───────────────────────────────────────────────────────────


async def _index(request: Request):
    index_file = _DIST_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse(
        _FALLBACK_HTML,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


async def _api_upload(request: Request):
    _ensure_dirs()
    form = await request.form()
    file = form.get("file")
    if file is None:
        return JSONResponse({"error": "no file"}, status_code=400)
    safe_name = Path(file.filename).name
    dest = INBOX_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)
    incoming.put(
        {
            "content": f"[File uploaded: {safe_name}]",
            "filename": safe_name,
            "author": "web-user",
        }
    )
    _broadcast(json.dumps({"type": "file_dropped", "filename": safe_name, "ts": _ts()}))
    return JSONResponse({"status": "ok", "filename": safe_name})


async def _api_outbox_list(request: Request):
    _ensure_dirs()
    files = []
    try:
        for p in sorted(OUTBOX_DIR.iterdir()):
            if p.is_file():
                st = p.stat()
                files.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    except OSError as e:
        log.warning("outbox list error: %s", e)
    return JSONResponse(files)


async def _api_outbox_download(request: Request):
    safe = Path(request.path_params["filename"]).name
    path = OUTBOX_DIR / safe
    if not path.exists():
        return Response("Not found", status_code=404)
    return FileResponse(str(path), filename=safe)


async def _api_cc_send(request: Request):
    """CC->channel: Claude Code injects a message with author 'claude-code'."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "empty content"}, status_code=400)
    global _last_input_ts
    _last_input_ts = time.monotonic()
    incoming.put({"content": content, "author": "claude-code"})
    _broadcast(
        json.dumps(
            {
                "type": "message",
                "author": "claude-code",
                "content": content,
                "ts": _ts(),
            }
        )
    )
    _channel_append("claude-code", content)
    return JSONResponse({"status": "ok"})


async def _api_health(request: Request):
    """GET /health — platform liveness probe."""
    now = time.monotonic()
    uptime_s = round(now - _boot_ts, 1)
    last_input_ago_s = round(now - _last_input_ts, 1) if _last_input_ts > 0 else None
    with _agents_lock:
        agents = list(_agents.keys())
    with _client_lock:
        ws_clients = sum(len(qs) for qs in _session_clients.values())
    return JSONResponse(
        {
            "status": "ok",
            "uptime_s": uptime_s,
            "boot_ts": _boot_wall,
            "last_input_ago_s": last_input_ago_s,
            "active_threads": threading.active_count(),
            "ws_clients": ws_clients,
            "attached_agents": agents,
            "pid": os.getpid(),
            "ts": _ts(),
        }
    )


def _swap_pct() -> float | None:
    """Read swap usage % from /proc/meminfo. Returns None if unavailable."""
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("SwapTotal", 0)
        if total == 0:
            return 0.0
        free = info.get("SwapFree", 0)
        return round((total - free) / total * 100, 1)
    except Exception:
        return None


async def _api_metrics(request: Request):
    """GET /metrics — platform metrics snapshot."""
    now = time.monotonic()
    payload = {
        "uptime_s": round(now - _boot_ts, 1),
        "active_threads": threading.active_count(),
        "swap_pct": _swap_pct(),
        "ts": _ts(),
    }
    # Include last-pushed agent stats if any
    with _agents_lock:
        for agent_id, stats in _agent_stats.items():
            payload[f"agent_{agent_id}"] = stats
    return JSONResponse(payload)


async def _api_dashboard(request: Request):
    """GET /api/dashboard — returns last stats pushed by the primary attached agent."""
    with _agents_lock:
        # Return first agent's stats (typically Igor)
        for agent_id, stats in _agent_stats.items():
            data = dict(stats)
            data["ts"] = _ts()
            data["agent"] = agent_id
            return JSONResponse(data)
    return JSONResponse({"ts": _ts(), "status": "no agent attached"})


async def _api_sessions(request: Request):
    """GET /api/sessions — list active sessions and their client counts."""
    with _client_lock:
        sessions = {sid: len(qs) for sid, qs in _session_clients.items() if qs}
    return JSONResponse({"sessions": sessions})


# ── HTML dashboard + metrics pages ────────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard — Agentic Rack Server</title>
<style>
  body { font-family: monospace; background: #1a1a2e; color: #e0e0e0; padding: 2rem; }
  h1 { color: #7ec8e3; margin-bottom: 1rem; font-size: 1.2rem; }
  .card { background: #2a2a3e; border: 1px solid #444; padding: 1rem; margin: 0.5rem 0;
          border-radius: 4px; }
  .card h2 { color: #90ee90; font-size: 1rem; margin-bottom: 0.5rem; }
  .stat { display: inline-block; margin-right: 1.5rem; }
  .stat .label { color: #888; font-size: 0.85rem; }
  .stat .value { color: #e0e0e0; font-size: 1.1rem; font-weight: bold; }
  .agent { border-left: 3px solid #4caf50; padding-left: 0.8rem; margin: 0.5rem 0; }
  .agent.none { border-color: #555; color: #888; }
  a { color: #7ec8e3; }
  #data { white-space: pre-wrap; }
</style></head><body>
<h1>Agentic Rack Server — Dashboard</h1>
<div id="platform" class="card"><h2>Platform</h2><div id="plat-stats">loading...</div></div>
<div id="agents" class="card"><h2>Attached Agents</h2><div id="agent-list">loading...</div></div>
<div id="agent-data" class="card"><h2>Agent Data</h2><div id="data">loading...</div></div>
<p style="margin-top:1rem;font-size:0.8rem;color:#555"><a href="/">Chat</a> | <a href="/dashboard">Dashboard</a> | <a href="/metrics">Metrics</a></p>
<script>
async function refresh() {
  try {
    const h = await (await fetch('/health')).json();
    document.getElementById('plat-stats').innerHTML =
      '<span class="stat"><span class="label">uptime</span> <span class="value">' + Math.round(h.uptime_s) + 's</span></span>' +
      '<span class="stat"><span class="label">ws clients</span> <span class="value">' + h.ws_clients + '</span></span>' +
      '<span class="stat"><span class="label">threads</span> <span class="value">' + h.active_threads + '</span></span>' +
      '<span class="stat"><span class="label">pid</span> <span class="value">' + h.pid + '</span></span>';
    const aa = h.attached_agents || [];
    document.getElementById('agent-list').innerHTML = aa.length
      ? aa.map(a => '<div class="agent">' + a + '</div>').join('')
      : '<div class="agent none">No agents attached</div>';
  } catch(e) { document.getElementById('plat-stats').textContent = 'Error: ' + e; }
  try {
    const d = await (await fetch('/api/dashboard')).json();
    document.getElementById('data').textContent = JSON.stringify(d, null, 2);
  } catch(e) {}
}
refresh(); setInterval(refresh, 3000);
</script></body></html>"""


_METRICS_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Metrics — Agentic Rack Server</title>
<style>
  body { font-family: monospace; background: #1a1a2e; color: #e0e0e0; padding: 2rem; }
  h1 { color: #7ec8e3; margin-bottom: 1rem; font-size: 1.2rem; }
  pre { background: #2a2a3e; border: 1px solid #444; padding: 1rem; border-radius: 4px;
        overflow-x: auto; font-size: 0.9rem; }
  a { color: #7ec8e3; }
</style></head><body>
<h1>Agentic Rack Server — Metrics</h1>
<pre id="data">loading...</pre>
<p style="margin-top:1rem;font-size:0.8rem;color:#555"><a href="/">Chat</a> | <a href="/dashboard">Dashboard</a> | <a href="/metrics">Metrics</a></p>
<script>
async function refresh() {
  try {
    const m = await (await fetch('/api/metrics')).json();
    document.getElementById('data').textContent = JSON.stringify(m, null, 2);
  } catch(e) { document.getElementById('data').textContent = 'Error: ' + e; }
}
refresh(); setInterval(refresh, 5000);
</script></body></html>"""


async def _page_dashboard(request: Request):
    """GET /dashboard — HTML dashboard page."""
    return HTMLResponse(_DASHBOARD_HTML)


async def _page_metrics(request: Request):
    """GET /metrics-page — HTML metrics page (distinct from JSON /metrics)."""
    return HTMLResponse(_METRICS_HTML)


# ── Agent registration ───────────────────────────────────────────────────────


async def _api_agent_register(request: Request):
    """POST /api/agents/register — agent announces itself."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    agent_id = body.get("agent_id", "").strip()
    if not agent_id:
        return JSONResponse({"error": "agent_id required"}, status_code=400)
    capabilities = body.get("capabilities", [])
    callback_url = body.get("callback_url", "")
    tmux_target = body.get("tmux_target", "").strip()[:128]
    with _agents_lock:
        _agents[agent_id] = {
            "registered_at": _ts(),
            "capabilities": capabilities,
            "callback_url": callback_url,
            "tmux_target": tmux_target,
            "last_heartbeat": time.monotonic(),
        }
    log.info("Agent registered: %s (capabilities: %s)", agent_id, capabilities)
    _save_agents()
    # T-uc-comms-default-channels: auto-create agent channel on connect
    if _comms:
        _comms.ensure_channel(
            f"comms://{agent_id}",
            notify=True,
            retention="1y",
        )
    _broadcast(
        json.dumps(
            {
                "type": "agent_status",
                "agent_id": agent_id,
                "status": "attached",
                "ts": _ts(),
            }
        )
    )
    return JSONResponse({"status": "ok", "agent_id": agent_id})


async def _api_agent_deregister(request: Request):
    """POST /api/agents/deregister — agent disconnects."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    agent_id = body.get("agent_id", "").strip()
    with _agents_lock:
        _agents.pop(agent_id, None)
        _agent_stats.pop(agent_id, None)
    log.info("Agent deregistered: %s", agent_id)
    _save_agents()
    _broadcast(
        json.dumps(
            {
                "type": "agent_status",
                "agent_id": agent_id,
                "status": "detached",
                "ts": _ts(),
            }
        )
    )
    return JSONResponse({"status": "ok"})


async def _api_agent_stats(request: Request):
    """POST /api/agents/{id}/stats — agent pushes dashboard data."""
    agent_id = request.path_params.get("agent_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    with _agents_lock:
        if agent_id not in _agents:
            return JSONResponse({"error": "agent not registered"}, status_code=404)
        _agents[agent_id]["last_heartbeat"] = time.monotonic()
        _agent_stats[agent_id] = body
    # Broadcast dashboard update to all WS clients
    _broadcast(
        json.dumps({"type": "dashboard", "agent": agent_id, **body, "ts": _ts()})
    )
    return JSONResponse({"status": "ok"})


async def _api_agent_send(request: Request):
    """POST /api/agents/{id}/send — agent sends a message to web UI."""
    agent_id = request.path_params.get("agent_id", "")
    try:
        body = await request.json()
    except Exception:
        log.warning("uc_deliver: POST /api/agents/%s/send — invalid JSON", agent_id)
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    content = body.get("content", "").strip()
    session_id = body.get("session_id", "shared")
    if not content:
        log.warning(
            "uc_deliver: POST /api/agents/%s/send session=%s — empty content",
            agent_id,
            session_id,
        )
        return JSONResponse({"error": "empty content"}, status_code=400)
    log.info(
        "uc_deliver: POST accepted agent=%s session=%s len=%d",
        agent_id,
        session_id,
        len(content),
    )
    agent_send(content, agent_id, session_id)
    return JSONResponse({"status": "ok"})


async def _api_agent_poll(request: Request):
    """GET /api/agents/{id}/poll — agent polls for incoming messages.

    Returns messages from the incoming queue addressed to this agent.
    Non-blocking: returns empty list if no messages.
    """
    messages = []
    try:
        while not incoming.empty():
            msg = incoming.get_nowait()
            messages.append(msg)
    except Exception as e:
        log.debug("incoming queue drain error (non-fatal): %s", e)
    return JSONResponse({"messages": messages})


# ── WebSocket endpoint ───────────────────────────────────────────────────────


async def _ws_endpoint(ws: WebSocket):
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    current_session = "comms://shared"
    with _client_lock:
        _session_clients.setdefault(current_session, []).append(q)
        _client_session[id(ws)] = current_session

    # Send session history to newly joined client
    with _client_lock:
        hist = list(_session_history.get(current_session, []))
    if hist:
        await ws.send_text(
            json.dumps(
                {
                    "type": "session_history",
                    "session_id": current_session,
                    "messages": hist,
                }
            )
        )

    # Send agent status
    with _agents_lock:
        agents = list(_agents.keys())
    await ws.send_text(
        json.dumps(
            {
                "type": "platform_status",
                "attached_agents": agents,
                "ts": _ts(),
            }
        )
    )

    async def _receive():
        nonlocal current_session
        try:
            while True:
                data = await ws.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")

                if mtype == "identify":
                    _iname = (msg.get("name") or "").strip()[:60]
                    if _iname:
                        incoming.put(
                            {
                                "content": f"__identify__:{_iname}",
                                "author": _iname,
                                "client_id": id(ws),
                                "session_id": current_session,
                            }
                        )

                elif mtype == "join_session":
                    new_sid = (msg.get("session_id") or "shared").strip()[
                        :64
                    ] or "shared"
                    new_sid = _canonical_session_id(new_sid)
                    with _client_lock:
                        old_qs = _session_clients.get(current_session, [])
                        if q in old_qs:
                            old_qs.remove(q)
                        _session_clients.setdefault(new_sid, []).append(q)
                        _client_session[id(ws)] = new_sid
                        hist = list(_session_history.get(new_sid, []))
                        subscriber_count = len(_session_clients.get(new_sid, []))
                    log.info(
                        "uc_deliver: join_session client=%s %s -> %s "
                        "(new_session now has %d subscriber(s))",
                        id(ws),
                        current_session,
                        new_sid,
                        subscriber_count,
                    )
                    current_session = new_sid
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "session_history",
                                "session_id": new_sid,
                                "messages": hist,
                            }
                        )
                    )

                elif mtype == "message":
                    content = msg.get("content", "").strip()
                    author = msg.get("author", "web-user")
                    if content:
                        global _last_input_ts
                        _last_input_ts = time.monotonic()
                        incoming.put(
                            {
                                "content": content,
                                "author": author,
                                "client_id": id(ws),
                                "session_id": current_session,
                            }
                        )
                        umsg = {
                            "type": "message",
                            "author": author,
                            "content": content,
                            "ts": _ts(),
                            "session_id": current_session,
                        }
                        _add_to_history(current_session, umsg)
                        _broadcast_to_session(current_session, json.dumps(umsg))
                        _channel_append("comms://akien/web", content)
                        _deliver_to_tmux(content, author, current_session)
        except Exception as e:
            log.debug("ws receive error: %s", e)

    async def _forward():
        try:
            while True:
                payload = await q.get()
                try:
                    await ws.send_text(payload)
                except Exception as send_exc:
                    preview = payload[:80].replace("\n", " ")
                    log.warning(
                        "uc_deliver: ws.send_text FAILED client=%s session=%s: %s "
                        "(payload preview: %s)",
                        id(ws),
                        current_session,
                        send_exc,
                        preview,
                    )
                    raise
        except Exception as e:
            log.debug("ws forward loop ended for client=%s: %s", id(ws), e)

    recv = asyncio.ensure_future(_receive())
    fwd = asyncio.ensure_future(_forward())
    await asyncio.wait([recv, fwd], return_when=asyncio.FIRST_COMPLETED)
    for t in (recv, fwd):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    with _client_lock:
        qs = _session_clients.get(current_session, [])
        if q in qs:
            qs.remove(q)
        _client_session.pop(id(ws), None)
        remaining = len(_session_clients.get(current_session, []))
    log.info(
        "uc_deliver: disconnect client=%s session=%s (remaining subscribers=%d)",
        id(ws),
        current_session,
        remaining,
    )


# ── Starlette app factory ───────────────────────────────────────────────────


async def _api_dicksimnel_chat_post(request: Request):
    """POST /api/dicksimnel/chat — send a message to DickSimnel, get a response."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)
    try:
        from devices.dicksimnel.device import DickSimnelDevice
        device = DickSimnelDevice()
        response = device.chat(message)
    except Exception as exc:
        log.warning("_api_dicksimnel_chat: device error: %s", exc)
        response = f"DickSimnel unavailable: {exc}"
    ts = _ts()
    entry = {"role": "user", "content": message, "ts": ts}
    reply = {"role": "dicksimnel", "content": response, "ts": ts}
    with _ds_chat_lock:
        _ds_chat_history.extend([entry, reply])
        if len(_ds_chat_history) > 100:
            del _ds_chat_history[:-100]
    log.info("_api_dicksimnel_chat: message=%r response_len=%d", message[:40], len(response))
    return JSONResponse({"response": response, "ts": ts})


async def _api_dicksimnel_chat_get(request: Request):
    """GET /api/dicksimnel/chat — return recent DickSimnel conversation history."""
    try:
        limit = int(request.query_params.get("limit", "20"))
    except ValueError:
        limit = 20
    limit = max(1, min(limit, 100))
    with _ds_chat_lock:
        messages = list(_ds_chat_history[-limit:])
    return JSONResponse({"messages": messages, "count": len(messages)})


async def _api_comms_channels(request: Request):
    """GET /api/comms/channels — list all registered comms channels."""
    if not _comms:
        return JSONResponse({"channels": []})
    channels = _comms.list_channels()
    return JSONResponse(
        {
            "channels": [
                {
                    "address": ch.address,
                    "direction": ch.direction.value,
                    "delivery": ch.delivery.value,
                    "notify": ch.notify,
                    "retention": ch.retention,
                    "created_at": ch.created_at,
                    "last_active": ch.last_active,
                }
                for ch in channels
            ]
        }
    )


async def _api_comms_health(request: Request):
    """GET /api/comms/health — comms module health."""
    if not _comms:
        return JSONResponse({"online": False, "reason": "not initialized"})
    return JSONResponse(_comms.health())


async def _api_granny_health(request: Request):
    """GET /api/granny/health — Granny Weatherwax operational health snapshot."""
    try:
        from devices.granny.health import granny_health_snapshot

        snapshot = granny_health_snapshot()
        log.info("_api_granny_health: snapshot served")
        return JSONResponse(snapshot)
    except Exception as e:
        log.warning("_api_granny_health: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Palace browser ───────────────────────────────────────────────────────────
# Read-only palace / rack views. Require IGOR_HOME_DB_URL. Graceful when absent.

_NAV = (
    '<nav style="margin-bottom:1.5rem;font-size:0.85rem">'
    '<a href="/">Chat</a> · '
    '<a href="/rack">Rack</a> · '
    '<a href="/palace">Palace</a> · '
    '<a href="/decisions">Decisions</a> · '
    '<a href="/goals">Goals</a> · '
    '<a href="/questions">Questions</a> · '
    '<a href="/hypotheses">Hypotheses</a> · '
    '<a href="/outcomes">Outcomes</a> · '
    '<a href="/queue">Queue</a> · '
    '<a href="/dashboard">Dashboard</a>'
    "</nav>"
)

_PAGE_CSS = (
    "<style>"
    "body{font-family:monospace;background:#1a1a2e;color:#e0e0e0;padding:2rem;max-width:1100px;margin:0 auto}"
    "h1{color:#7ec8e3;font-size:1.2rem;margin-bottom:0.5rem}"
    "h2{color:#90ee90;font-size:1rem;margin:1rem 0 0.4rem}"
    "a{color:#7ec8e3;text-decoration:none}"
    "a:hover{text-decoration:underline}"
    "table{border-collapse:collapse;width:100%;margin:0.5rem 0}"
    "th{background:#2a2a3e;color:#90ee90;text-align:left;padding:0.4rem 0.6rem;font-size:0.85rem}"
    "td{padding:0.3rem 0.6rem;border-bottom:1px solid #333;font-size:0.85rem;vertical-align:top}"
    "tr:hover td{background:#252535}"
    ".ok{color:#90ee90}.warn{color:#f0c040}.err{color:#e05050}"
    "pre{background:#2a2a3e;border:1px solid #444;padding:1rem;border-radius:4px;"
    "overflow-x:auto;white-space:pre-wrap;word-break:break-word;font-size:0.85rem}"
    ".badge{display:inline-block;padding:0.1rem 0.4rem;border-radius:3px;font-size:0.8rem;"
    "background:#333;margin-right:0.3rem}"
    ".no-db{background:#2a2a3e;border:1px solid #555;padding:1rem;border-radius:4px;color:#888}"
    "nav a{margin-right:0.3rem}"
    "</style>"
)


def _html_wrap(title: str, body: str) -> str:
    return (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title} — ADC</title>{_PAGE_CSS}</head>"
        f"<body>{_NAV}<h1>{title}</h1>{body}</body></html>"
    )


def _db_conn():
    """Return a psycopg2 connection or None when IGOR_HOME_DB_URL is absent."""
    db_url = os.environ.get("IGOR_HOME_DB_URL", "")
    if not db_url:
        return None
    try:
        import psycopg2

        return psycopg2.connect(db_url)
    except Exception as exc:
        log.debug("palace browser: DB connect failed — %s", exc)
        return None


def _no_db_msg() -> str:
    return '<div class="no-db">IGOR_HOME_DB_URL not set — DB unavailable</div>'


def _load_device_registry() -> list[dict]:
    """Read the flat-file device registry from _RUNTIME_ROOT/devices.json."""
    registry_path = _RUNTIME_ROOT / "devices.json"
    try:
        data = json.loads(registry_path.read_text())
        return list(data.values()) if isinstance(data, dict) else []
    except Exception as exc:
        log.debug("device registry read failed: %s", exc)
        return []


_RACK_PAGE_BODY = """
<p id="rack-ts" style="color:#555;font-size:0.8rem;margin-bottom:1rem">Loading...</p>

<h2>Web Server</h2>
<table id="ws-table">
  <tr><th>Uptime</th><th>Boot</th><th>PID</th><th>WS clients</th><th>Threads</th></tr>
  <tr id="ws-row"><td colspan="5" style="color:#888">loading...</td></tr>
</table>

<h2>OpenRouter Budget</h2>
<div id="budget-inner" style="color:#888">loading...</div>

<h2>Rack Devices</h2>
<p style="color:#666;font-size:0.82rem;margin:0 0 0.5rem">
  From flat-file registry. Expand to see full device record and any pushed stats.</p>
<div id="devices-wrap" style="color:#888">loading...</div>

<h2>Machines</h2>
<div id="machines-wrap" style="color:#888">loading...</div>

<p style="margin-top:1.5rem;font-size:0.75rem;color:#555">
  Auto-refreshes every 10s &middot; <a href="/rack">Force refresh</a></p>

<script>
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function badge(s){
  var c=s==='online'?'ok':s==='blocked'||s==='offline'?'err':'warn';
  return '<span class="'+c+'">'+esc(s)+'</span>';
}
function renderWS(ws){
  document.getElementById('ws-row').innerHTML=
    '<td>'+ws.uptime_s+'s</td><td>'+esc(ws.boot_ts)+'</td><td>'+ws.pid+'</td>'+
    '<td>'+ws.ws_clients+'</td><td>'+ws.active_threads+'</td>';
}
function renderBudget(b){
  var el=document.getElementById('budget-inner');
  if(!b){el.innerHTML='<p style="color:#888">No budget data (DB unavailable)</p>';return;}
  var c=b.balance>15?'ok':b.balance>5?'warn':'err';
  el.innerHTML='<table><tr><th>Balance</th><th>Purchased</th><th>Used</th><th>As of</th></tr>'+
    '<tr><td class="'+c+'">$'+b.balance.toFixed(2)+'</td>'+
    '<td>$'+b.purchased.toFixed(2)+'</td><td>$'+b.used.toFixed(2)+'</td>'+
    '<td>'+esc(b.as_of)+'</td></tr></table>';
}
function deviceDetail(dev){
  var d={id:dev.id,name:dev.name,status:dev.status,mailbox:dev.mailbox,
         registered_at:dev.registered_at,config:dev.config};
  if(dev.last_heartbeat_ago_s!==undefined) d.last_heartbeat_ago_s=dev.last_heartbeat_ago_s;
  if(dev.agent_stats) d.pushed_stats=dev.agent_stats;
  return JSON.stringify(d,null,2);
}
function deviceCard(dev){
  var hb=dev.last_heartbeat_ago_s!==undefined
    ?' <span style="color:#666;font-size:0.78rem">(hb '+dev.last_heartbeat_ago_s+'s ago)</span>':'';
  return '<details style="margin:0.2rem 0">'+
    '<summary style="cursor:pointer">'+
      '<strong>'+esc(dev.name||dev.id)+'</strong> '+badge(dev.status)+hb+
      ' <span style="color:#666;font-size:0.78rem">'+esc(dev.mailbox)+'</span>'+
    '</summary>'+
    '<pre style="margin:0.4rem 0 0.4rem 1.5rem;font-size:0.82rem">'+esc(deviceDetail(dev))+'</pre>'+
    '</details>';
}
function renderDevices(devices){
  var el=document.getElementById('devices-wrap');
  if(!devices||!devices.length){
    el.innerHTML='<p style="color:#888">No devices in registry.</p>';return;
  }
  el.innerHTML=devices.map(deviceCard).join('');
}
function renderMachines(machines,devices,localHostname){
  var el=document.getElementById('machines-wrap');
  if(!machines||!machines.length){
    el.innerHTML='<p style="color:#888">No machines (DB unavailable or no machines registered)</p>';return;
  }
  var rows=machines.map(function(m){
    var c=m.status==='online'?'ok':'err';
    var roles=(m.roles||[]).join(', ')||'—';
    var devBlock='';
    if(m.hostname===localHostname){
      if(devices&&devices.length){
        devBlock='<tr><td colspan="6" style="padding:0.2rem 0.6rem 0.6rem 2rem;background:#1e1e30">'+
          '<div style="font-size:0.78rem;color:#7ec8e3;margin-bottom:0.3rem">Registered devices:</div>'+
          devices.map(deviceCard).join('')+'</td></tr>';
      } else {
        devBlock='<tr><td colspan="6" style="padding:0.15rem 0.6rem 0.3rem 2rem;color:#555;font-size:0.8rem">'+
          'No devices in local registry.</td></tr>';
      }
    } else {
      devBlock='<tr><td colspan="6" style="padding:0.1rem 0.6rem 0.2rem 2rem;color:#555;font-size:0.78rem">'+
        '(remote — device registry not accessible from here)</td></tr>';
    }
    return '<tr><td>'+esc(m.display_name)+'</td><td>'+esc(m.hostname)+'</td>'+
      '<td>'+esc(m.ip||'—')+'</td><td>'+esc(m.os)+'</td>'+
      '<td class="'+c+'">'+esc(m.status)+'</td><td>'+esc(roles)+'</td></tr>'+devBlock;
  });
  el.innerHTML='<table><tr><th>Name</th><th>Hostname</th><th>IP</th>'+
    '<th>OS</th><th>Status</th><th>Roles</th></tr>'+rows.join('')+'</table>';
}
async function refresh(){
  try{
    var data=await(await fetch('/api/rack/health')).json();
    document.getElementById('rack-ts').textContent='Last updated: '+data.ts;
    renderWS(data.web_server);
    renderBudget(data.budget);
    renderDevices(data.devices);
    renderMachines(data.machines,data.devices,data.local_hostname);
  }catch(e){
    document.getElementById('rack-ts').textContent='Error loading rack health: '+e;
  }
}
refresh();
setInterval(refresh,10000);
</script>
"""


async def _page_rack(request: Request):
    """GET /rack — rack health page (JS-driven; data from /api/rack/health)."""
    return HTMLResponse(_html_wrap("Rack Health", _RACK_PAGE_BODY))


async def _api_rack_health(request: Request):
    """GET /api/rack/health — full rack health snapshot consumed by /rack page JS.

    Returns web server stats, registered devices from the flat-file registry
    (with any stats pushed via /api/agents/{id}/stats), machines and OR budget
    from DB, and local_hostname for machine-to-device grouping in JS.
    """
    import socket as _socket

    now = time.monotonic()
    with _client_lock:
        ws_clients = sum(len(qs) for qs in _session_clients.values())
    web_server_stats = {
        "uptime_s": round(now - _boot_ts, 1),
        "boot_ts": _boot_wall,
        "pid": os.getpid(),
        "ws_clients": ws_clients,
        "active_threads": threading.active_count(),
    }

    raw_devices = _load_device_registry()
    with _agents_lock:
        agent_stats_snap = dict(_agent_stats)
        agents_snap = dict(_agents)

    devices = []
    for rec in raw_devices:
        dev_id = rec.get("id", "")
        entry: dict = {
            "id": dev_id,
            "name": rec.get("name", dev_id),
            "status": rec.get("status", "unknown"),
            "mailbox": rec.get("mailbox", ""),
            "registered_at": rec.get("registered_at", ""),
            "config": rec.get("config", {}),
            "agent_stats": agent_stats_snap.get(dev_id),
        }
        if dev_id in agents_snap:
            hb = agents_snap[dev_id].get("last_heartbeat")
            if hb is not None:
                entry["last_heartbeat_ago_s"] = round(now - hb, 1)
        devices.append(entry)
    devices.sort(key=lambda d: (0 if d["status"] == "online" else 1, d["name"]))

    machines: list[dict] = []
    budget = None
    conn = _db_conn()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT display_name, hostname, ip, os, status, roles, updated_at"
                    " FROM infra.machines ORDER BY status DESC, display_name"
                )
                cols = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    m = dict(zip(cols, row))
                    if m.get("updated_at"):
                        m["updated_at"] = str(m["updated_at"])[:19]
                    machines.append(m)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT balance, purchased, used, timestamp"
                    " FROM infra.balance_history ORDER BY timestamp DESC LIMIT 1"
                )
                brow = cur.fetchone()
                if brow:
                    balance, purchased, used, bts = brow
                    budget = {
                        "balance": float(balance),
                        "purchased": float(purchased),
                        "used": float(used),
                        "as_of": str(bts)[:19],
                    }
        except Exception as exc:
            log.debug("rack health DB query failed: %s", exc)
        finally:
            conn.close()

    return JSONResponse(
        {
            "web_server": web_server_stats,
            "devices": devices,
            "machines": machines,
            "budget": budget,
            "local_hostname": _socket.gethostname(),
            "ts": _ts(),
        }
    )


async def _page_palace(request: Request):
    """GET /palace — full adc.palace tree listing."""
    conn = _db_conn()
    if not conn:
        return HTMLResponse(_html_wrap("Palace", _no_db_msg()))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path, title, node_type, updated_at FROM adc.palace ORDER BY path"
            )
            nodes = cur.fetchall()
    except Exception as exc:
        conn.close()
        return HTMLResponse(_html_wrap("Palace", f'<p class="err">DB error: {exc}</p>'))
    finally:
        conn.close()

    # Group by top-level prefix
    from collections import defaultdict

    groups: dict = defaultdict(list)
    for path, title, ntype, updated in nodes:
        prefix = path.split(".")[0] if "." in path else path
        groups[prefix].append((path, title or "", ntype or "", updated))

    sections = [f"<p style='color:#888'>{len(nodes)} nodes</p>"]
    for prefix in sorted(groups):
        rows = []
        for path, title, ntype, updated in sorted(groups[prefix]):
            up = str(updated)[:10] if updated else ""
            safe_path = path.replace('"', "&quot;")
            rows.append(
                f'<tr><td><a href="/palace/{safe_path}">{path}</a></td>'
                f"<td>{title}</td><td>{ntype}</td><td>{up}</td></tr>"
            )
        sections.append(
            f"<h2>{prefix} ({len(groups[prefix])})</h2>"
            "<table><tr><th>Path</th><th>Title</th><th>Type</th><th>Updated</th></tr>"
            + "".join(rows)
            + "</table>"
        )
    return HTMLResponse(_html_wrap("Palace", "".join(sections)))


async def _page_palace_node(request: Request):
    """GET /palace/{path} — render a single palace node."""
    node_path = request.path_params.get("node_path", "")
    conn = _db_conn()
    if not conn:
        return HTMLResponse(_html_wrap(f"Palace: {node_path}", _no_db_msg()))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path, title, content, node_type, updated_at, metadata"
                " FROM adc.palace WHERE path = %s",
                (node_path,),
            )
            row = cur.fetchone()
    except Exception as exc:
        conn.close()
        return HTMLResponse(
            _html_wrap(node_path, f'<p class="err">DB error: {exc}</p>')
        )
    finally:
        conn.close()

    if not row:
        return HTMLResponse(
            _html_wrap(node_path, f'<p class="err">Node not found: {node_path}</p>'),
            status_code=404,
        )
    path, title, content, ntype, updated, metadata = row
    import html as _html_mod

    safe_content = _html_mod.escape(content or "")
    meta_html = ""
    if metadata:
        meta_html = f"<pre>{_html_mod.escape(str(metadata))}</pre>"
    edit_link = (
        f' · <a href="/palace-edit/{_html_mod.escape(node_path)}">Edit</a>'
        if os.environ.get("ADC_EDIT_TOKEN")
        else ""
    )
    body = (
        f"<p style='color:#888'>{ntype} · updated {str(updated)[:19]}{edit_link}</p>"
        f"<pre>{safe_content}</pre>"
        + (f"<h2>Metadata</h2>{meta_html}" if meta_html else "")
        + f'<p style="margin-top:1rem"><a href="/palace">← Back to palace</a></p>'
    )
    return HTMLResponse(_html_wrap(title or path, body))


def _check_edit_token(token: str) -> bool:
    """Return True if token matches ADC_EDIT_TOKEN env var."""
    expected = os.environ.get("ADC_EDIT_TOKEN", "")
    return bool(expected and token == expected)


async def _page_palace_edit_get(request: Request):
    """GET /palace-edit/{path} — edit form for a palace node."""
    if not os.environ.get("ADC_EDIT_TOKEN"):
        return HTMLResponse(
            _html_wrap("Edit Disabled", "<p>Set ADC_EDIT_TOKEN to enable editing.</p>"),
            status_code=403,
        )
    node_path = request.path_params.get("node_path", "")
    conn = _db_conn()
    if not conn:
        return HTMLResponse(_html_wrap(f"Edit: {node_path}", _no_db_msg()))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path, title, content FROM adc.palace WHERE path = %s",
                (node_path,),
            )
            row = cur.fetchone()
    except Exception as exc:
        conn.close()
        return HTMLResponse(
            _html_wrap(node_path, f'<p class="err">DB error: {exc}</p>')
        )
    finally:
        conn.close()

    import html as _html_mod

    if not row:
        return HTMLResponse(
            _html_wrap(node_path, f'<p class="err">Node not found: {node_path}</p>'),
            status_code=404,
        )
    _, title, content = row
    safe_path = _html_mod.escape(node_path)
    safe_title = _html_mod.escape(title or "")
    safe_content = _html_mod.escape(content or "")
    body = (
        f'<p style="color:#888">Editing <code>{safe_path}</code></p>'
        f'<form method="POST" action="/palace-edit/{safe_path}">'
        f'<div style="margin-bottom:.5rem">'
        f"<label>Title<br>"
        f'<input name="title" value="{safe_title}" style="width:100%;background:#1a1a1a;color:#e0e0e0;border:1px solid #444;padding:.3rem"></label></div>'
        f'<div style="margin-bottom:.5rem">'
        f"<label>Content<br>"
        f'<textarea name="content" rows="20" style="width:100%;background:#1a1a1a;color:#e0e0e0;border:1px solid #444;padding:.3rem;font-family:monospace">{safe_content}</textarea></label></div>'
        f'<div style="margin-bottom:.5rem">'
        f"<label>Token<br>"
        f'<input type="password" name="_token" placeholder="ADC_EDIT_TOKEN" style="width:16rem;background:#1a1a1a;color:#e0e0e0;border:1px solid #444;padding:.3rem"></label></div>'
        f'<button type="submit" style="background:#2a6a2a;color:#e0e0e0;border:none;padding:.4rem 1rem;cursor:pointer">Save</button>'
        f' <a href="/palace/{safe_path}" style="margin-left:.5rem">Cancel</a>'
        f"</form>"
    )
    return HTMLResponse(_html_wrap(f"Edit: {title or node_path}", body))


async def _page_palace_edit_post(request: Request):
    """POST /palace-edit/{path} — save updated palace node content."""
    if not os.environ.get("ADC_EDIT_TOKEN"):
        return HTMLResponse(
            _html_wrap("Edit Disabled", "<p>Set ADC_EDIT_TOKEN to enable editing.</p>"),
            status_code=403,
        )
    node_path = request.path_params.get("node_path", "")
    try:
        form = await request.form()
    except Exception:
        return HTMLResponse(
            _html_wrap("Error", "<p>Invalid form data.</p>"), status_code=400
        )
    token = form.get("_token", "")
    if not _check_edit_token(token):
        return HTMLResponse(
            _html_wrap("Forbidden", "<p>Invalid token.</p>"), status_code=403
        )
    new_title = (form.get("title") or "").strip()
    new_content = (form.get("content") or "").strip()
    conn = _db_conn()
    if not conn:
        return HTMLResponse(_html_wrap("Edit", _no_db_msg()), status_code=503)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE adc.palace SET title=%s, content=%s, updated_at=now()"
                " WHERE path=%s",
                (new_title, new_content, node_path),
            )
            updated_rows = cur.rowcount
        conn.commit()
    except Exception as exc:
        conn.close()
        return HTMLResponse(
            _html_wrap("Edit Error", f'<p class="err">DB error: {exc}</p>'),
            status_code=500,
        )
    finally:
        conn.close()

    if updated_rows == 0:
        return HTMLResponse(
            _html_wrap("Not Found", f"<p>Node not found: {node_path}</p>"),
            status_code=404,
        )
    import html as _html_mod

    safe_path = _html_mod.escape(node_path)
    return HTMLResponse(
        _html_wrap(
            "Saved",
            f'<p>Saved <a href="/palace/{safe_path}">{safe_path}</a></p>',
        )
    )


async def _page_decisions(request: Request):
    """GET /decisions — list palace.decisions.* nodes."""
    conn = _db_conn()
    if not conn:
        return HTMLResponse(_html_wrap("Decisions", _no_db_msg()))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path, title, metadata->>'date', metadata->>'status',"
                " metadata->>'spawned_tickets'"
                " FROM adc.palace WHERE path LIKE 'palace.decisions.%'"
                " ORDER BY path DESC"
            )
            rows = cur.fetchall()
    except Exception as exc:
        conn.close()
        return HTMLResponse(
            _html_wrap("Decisions", f'<p class="err">DB error: {exc}</p>')
        )
    finally:
        conn.close()

    if not rows:
        return HTMLResponse(_html_wrap("Decisions", "<p>No decisions found.</p>"))
    tr = []
    for path, title, date, status, tickets in rows:
        d_id = path.split(".")[-1] if "." in path else path
        safe = path.replace('"', "&quot;")
        status_cls = "ok" if status == "closed" else "warn"
        tr.append(
            f'<tr><td><a href="/palace/{safe}">{d_id}</a></td>'
            f"<td>{title or ''}</td>"
            f"<td>{date or ''}</td>"
            f'<td class="{status_cls}">{status or "open"}</td>'
            f"<td style='font-size:0.8rem'>{tickets or ''}</td></tr>"
        )
    body = (
        f"<p style='color:#888'>{len(rows)} decisions</p>"
        "<table><tr><th>ID</th><th>Title</th><th>Date</th><th>Status</th><th>Tickets</th></tr>"
        + "".join(tr)
        + "</table>"
    )
    return HTMLResponse(_html_wrap("Decisions", body))


async def _page_goals(request: Request):
    """GET /goals — Akien's goals tree from palace.shared.akien.goals."""
    conn = _db_conn()
    if not conn:
        return HTMLResponse(_html_wrap("Goals", _no_db_msg()))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path, title, content, updated_at FROM adc.palace"
                " WHERE path LIKE 'palace.goals.%' OR path = 'palace.shared.akien.goals'"
                " ORDER BY path"
            )
            rows = cur.fetchall()
    except Exception as exc:
        conn.close()
        return HTMLResponse(_html_wrap("Goals", f'<p class="err">DB error: {exc}</p>'))
    finally:
        conn.close()

    import html as _html_mod

    sections = []
    for path, title, content, updated in rows:
        safe = _html_mod.escape(content or "")
        sections.append(
            f"<h2>{title or path}</h2>"
            f"<p style='color:#888'>Updated: {str(updated)[:19]}</p>"
            f"<pre>{safe}</pre>"
        )
    body = "".join(sections) if sections else "<p>No goals nodes found.</p>"
    return HTMLResponse(_html_wrap("Goals", body))


def _simple_palace_list(title: str, path_prefix: str) -> str:
    """Shared helper for questions / hypotheses / outcomes pages."""
    conn = _db_conn()
    if not conn:
        return _html_wrap(title, _no_db_msg())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path, title, left(content,200), updated_at FROM adc.palace"
                " WHERE path LIKE %s ORDER BY path DESC",
                (f"{path_prefix}%",),
            )
            rows = cur.fetchall()
    except Exception as exc:
        conn.close()
        return _html_wrap(title, f'<p class="err">DB error: {exc}</p>')
    finally:
        conn.close()

    if not rows:
        return _html_wrap(title, f"<p>No {title.lower()} nodes found yet.</p>")
    import html as _html_mod

    tr = []
    for path, t, snippet, updated in rows:
        safe = path.replace('"', "&quot;")
        tr.append(
            f'<tr><td><a href="/palace/{safe}">{path}</a></td>'
            f"<td>{t or ''}</td>"
            f"<td style='color:#888;font-size:0.8rem'>{_html_mod.escape(snippet or '')}</td>"
            f"<td>{str(updated)[:10] if updated else ''}</td></tr>"
        )
    body = (
        f"<p style='color:#888'>{len(rows)} {title.lower()}</p>"
        "<table><tr><th>Path</th><th>Title</th><th>Preview</th><th>Updated</th></tr>"
        + "".join(tr)
        + "</table>"
    )
    return _html_wrap(title, body)


async def _page_questions(request: Request):
    """GET /questions — list palace.questions.* nodes."""
    return HTMLResponse(_simple_palace_list("Questions", "palace.questions."))


async def _page_hypotheses(request: Request):
    """GET /hypotheses — list palace.hypotheses.* nodes."""
    return HTMLResponse(_simple_palace_list("Hypotheses", "palace.hypotheses."))


async def _page_outcomes(request: Request):
    """GET /outcomes — list palace.outcomes.* nodes."""
    return HTMLResponse(_simple_palace_list("Outcomes", "palace.outcomes."))


# ── Queue route ──────────────────────────────────────────────────────────────

_STATUS_ORDER = ["in_progress", "sprint", "design", "triage", "hold", "pending", "dependency", "approval", "akien"]
_STATUS_CLASS = {
    "in_progress": "ok",
    "sprint": "ok",
    "hold": "warn",
    "pending": "warn",
    "dependency": "warn",
    "triage": "",
    "design": "",
    "approval": "warn",
    "akien": "warn",
}


def _load_queue_tickets() -> list[dict]:
    """Load open tickets from clan.memories. Returns [] when DB unavailable."""
    conn = _db_conn()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    name,
                    narrative,
                    metadata->>'status'   AS status,
                    metadata->>'size'     AS size,
                    metadata->>'worker'   AS worker,
                    metadata->>'gate'     AS gate,
                    (metadata->>'priority')::float AS priority
                FROM clan.memories
                WHERE parent_id = 'TICKETS_ROOT'
                  AND metadata->>'kind' = 'ticket'
                  AND metadata->>'status' NOT IN ('closed', 'cancelled')
                ORDER BY
                    CASE metadata->>'status'
                        WHEN 'in_progress' THEN 0
                        WHEN 'sprint'      THEN 1
                        ELSE 2
                    END,
                    (metadata->>'priority')::float DESC NULLS LAST,
                    name
                """
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0] or "",
                "title": r[1] or "",
                "status": r[2] or "unknown",
                "size": r[3] or "?",
                "worker": r[4] or "",
                "gate": r[5] or "",
                "priority": float(r[6]) if r[6] is not None else 0.5,
            }
            for r in rows
        ]
    except Exception as exc:
        log.debug("queue: DB query failed — %s", exc)
        return []
    finally:
        conn.close()


async def _api_queue(request: Request):
    """GET /api/queue — open tickets as JSON, grouped by status."""
    log.info("queue: API request from %s", request.client)
    tickets = _load_queue_tickets()
    grouped: dict[str, list] = {}
    for t in tickets:
        grouped.setdefault(t["status"], []).append(t)
    return JSONResponse({"tickets": tickets, "grouped": grouped, "count": len(tickets)})


async def _page_queue(request: Request):
    """GET /queue — open ticket queue as HTML, grouped by status."""
    log.info("queue: page request from %s", request.client)
    conn = _db_conn()
    if not conn:
        return HTMLResponse(_html_wrap("Queue", _no_db_msg()))

    tickets = _load_queue_tickets()

    if not tickets:
        body = "<p>No open tickets.</p>"
        return HTMLResponse(_html_wrap("Queue", body))

    grouped: dict[str, list] = {}
    for t in tickets:
        grouped.setdefault(t["status"], []).append(t)

    sections = []
    seen_statuses = set(grouped.keys())
    order = [s for s in _STATUS_ORDER if s in seen_statuses] + sorted(seen_statuses - set(_STATUS_ORDER))
    for status in order:
        group = grouped[status]
        cls = _STATUS_CLASS.get(status, "")
        rows = []
        for t in group:
            gate_cell = f'<span style="color:#888;font-size:0.8rem">{t["gate"]}</span>' if t["gate"] else ""
            rows.append(
                f'<tr><td style="font-family:monospace">{t["id"]}</td>'
                f"<td>{t['title']}</td>"
                f'<td class="badge">{t["size"]}</td>'
                f'<td style="color:#888">{t["worker"]}</td>'
                f"<td>{gate_cell}</td></tr>"
            )
        sections.append(
            f'<h2><span class="{cls}">{status}</span> <span style="color:#888;font-size:0.85rem">({len(group)})</span></h2>'
            "<table><tr><th>ID</th><th>Title</th><th>Size</th><th>Worker</th><th>Gate</th></tr>"
            + "".join(rows)
            + "</table>"
        )

    refresh_js = (
        "<script>"
        "setTimeout(()=>location.reload(),30000);"
        "document.addEventListener('DOMContentLoaded',()=>{"
        "const el=document.createElement('span');"
        "el.id='refresh-countdown';"
        "el.style='color:#888;font-size:0.8rem;margin-left:1rem';"
        "document.querySelector('h1').appendChild(el);"
        "let s=30;const t=setInterval(()=>{el.textContent='(refresh in '+s+'s)';if(--s<0)clearInterval(t);},1000);"
        "});"
        "</script>"
    )
    body = (
        f"<p style='color:#888'>{len(tickets)} open tickets</p>"
        + "".join(sections)
        + refresh_js
    )
    return HTMLResponse(_html_wrap("Queue", body))


# ── Feeds route ──────────────────────────────────────────────────────────────

_feeds_imap = None
_feeds_imap_lock = threading.Lock()


def _get_feeds_imap():
    global _feeds_imap
    with _feeds_imap_lock:
        if _feeds_imap is None:
            try:
                from bus.imap_server import IMAPServer, _TEST_MODE

                s = IMAPServer()
                if not _TEST_MODE:
                    s.start()
                _feeds_imap = s
            except Exception as exc:
                log.warning("feeds: IMAP init failed: %s", exc)
    return _feeds_imap


async def _api_feeds(request: Request):
    """GET /feeds/{device} — last N events from device's feed mailbox as JSON."""
    device = request.path_params.get("device", "")
    if not device or "/" in device:
        return JSONResponse({"error": "invalid device"}, status_code=400)
    try:
        limit = min(int(request.query_params.get("limit", "20")), 100)
    except (ValueError, TypeError):
        limit = 20
    imap = _get_feeds_imap()
    if imap is None:
        return JSONResponse({"error": "feeds unavailable"}, status_code=503)
    mailbox = f"feeds/{device}"
    try:
        events = imap.fetch_recent(mailbox, limit)
    except Exception as exc:
        log.debug("feeds: fetch_recent %s failed: %s", mailbox, exc)
        return JSONResponse({"events": [], "count": 0, "device": device})
    result = [
        {
            "from": e.from_device,
            "to": e.to_device,
            "sent_at": e.sent_at,
            "payload": e.payload,
        }
        for e in events
    ]
    return JSONResponse({"events": result, "count": len(result), "device": device})


def _make_app() -> Starlette:
    @contextlib.asynccontextmanager
    async def _lifespan(app: Starlette):
        global _loop
        _loop = asyncio.get_running_loop()
        _init_comms()
        _load_agents()
        yield

    routes = [
        Route("/", _index),
        WebSocketRoute("/ws", _ws_endpoint),
        # Platform endpoints
        Route("/api/upload", _api_upload, methods=["POST"]),
        Route("/api/cc_send", _api_cc_send, methods=["POST"]),
        Route("/api/outbox", _api_outbox_list),
        Route("/api/outbox/{filename}", _api_outbox_download),
        Route("/health", _api_health),
        Route("/api/health", _api_health),
        Route("/metrics", _api_metrics),
        Route("/api/metrics", _api_metrics),
        Route("/api/dashboard", _api_dashboard),
        Route("/api/sessions", _api_sessions),
        # HTML pages
        Route("/dashboard", _page_dashboard),
        Route("/metrics-page", _page_metrics),
        # Agent management
        Route("/api/agents/register", _api_agent_register, methods=["POST"]),
        Route("/api/agents/deregister", _api_agent_deregister, methods=["POST"]),
        Route("/api/agents/{agent_id}/stats", _api_agent_stats, methods=["POST"]),
        Route("/api/agents/{agent_id}/send", _api_agent_send, methods=["POST"]),
        Route("/api/agents/{agent_id}/poll", _api_agent_poll),
        # Comms
        Route("/api/dicksimnel/chat", _api_dicksimnel_chat_post, methods=["POST"]),
        Route("/api/dicksimnel/chat", _api_dicksimnel_chat_get, methods=["GET"]),
        Route("/api/comms/channels", _api_comms_channels),
        Route("/api/comms/health", _api_comms_health),
        Route("/api/granny/health", _api_granny_health),
        # Rack health API + page
        Route("/api/rack/health", _api_rack_health),
        Route("/rack", _page_rack),
        Route("/palace", _page_palace),
        Route("/palace-edit/{node_path:path}", _page_palace_edit_get, methods=["GET"]),
        Route(
            "/palace-edit/{node_path:path}", _page_palace_edit_post, methods=["POST"]
        ),
        Route("/palace/{node_path:path}", _page_palace_node),
        Route("/decisions", _page_decisions),
        Route("/goals", _page_goals),
        Route("/questions", _page_questions),
        Route("/hypotheses", _page_hypotheses),
        Route("/outcomes", _page_outcomes),
        # Queue
        Route("/api/queue", _api_queue),
        Route("/queue", _page_queue),
        # Feeds
        Route("/feeds/{device}", _api_feeds),
    ]

    # Serve compiled Svelte assets if the UI has been built
    assets_dir = _DIST_DIR / "assets"
    if assets_dir.exists():
        routes.append(
            Mount("/assets", app=StaticFiles(directory=str(assets_dir)), name="assets")
        )

    return Starlette(routes=routes, lifespan=_lifespan)


# ── PID file management ─────────────────────────────────────────────────────


def _write_pid():
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    log.info("PID file written: %s (pid=%d)", PID_FILE, os.getpid())


def _remove_pid():
    try:
        if PID_FILE.exists():
            stored_pid = int(PID_FILE.read_text().strip())
            if stored_pid == os.getpid():
                PID_FILE.unlink()
                log.info("PID file removed")
    except Exception as e:
        log.warning("PID file cleanup error: %s", e)


def check_running() -> dict | None:
    """Check if another rack server instance is running.

    Returns health dict if running and healthy, None otherwise.
    Kills stalled instances (PID exists but health check fails).
    """
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None

    # On Windows, skip the PID-existence precheck: venv python.exe acts as a
    # launcher stub and the resulting PID-file value isn't always queryable
    # via OpenProcess from a different process context. Trust the HTTP check.
    # On Linux, a quick existence check avoids an HTTP timeout on dead PIDs.
    if not _IS_WINDOWS and not _process_exists(pid):
        log.info("Stale PID file (pid=%d not running), removing", pid)
        PID_FILE.unlink(missing_ok=True)
        return None

    # Process exists — check health
    # Try multiple URLs: SSL may be active (main port is HTTPS), and there
    # may be a plain HTTP fallback on a different port.
    port = int(os.environ.get("ADC_WEB_PORT") or os.environ.get("IGOR_UC_PORT", "8080"))
    http_port = int(os.environ.get("IGOR_UC_HTTP_PORT", "8082"))
    ssl_active = bool(os.environ.get("IGOR_SSL_CERT"))
    urls = []
    if ssl_active:
        urls.append(f"https://localhost:{port}/health")
    urls.append(f"http://localhost:{port}/health")
    if ssl_active:
        urls.append(f"http://localhost:{http_port}/health")
    import urllib.request
    import ssl as _ssl

    for url in urls:
        try:
            ctx = None
            if url.startswith("https://"):
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
                data = json.loads(resp.read())
                if data.get("status") == "ok":
                    return data
        except Exception as e:
            log.debug("health check %s failed (pid=%d): %s", url, pid, e)

    # Process exists but health check failed — stalled
    log.warning("Stalled rack server (pid=%d), killing", pid)
    _kill_process(pid)
    PID_FILE.unlink(missing_ok=True)
    return None


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Rack Server (D335)")
    parser.add_argument(
        "--port",
        type=int,
        default=int(
            os.environ.get("ADC_WEB_PORT") or os.environ.get("IGOR_UC_PORT", "8080")
        ),
    )
    parser.add_argument(
        "--check", action="store_true", help="Check if running, exit 0 if healthy"
    )
    parser.add_argument("--stop", action="store_true", help="Stop running instance")
    args = parser.parse_args()

    if args.check:
        health = check_running()
        if health:
            print(json.dumps(health, indent=2))
            sys.exit(0)
        else:
            print("Not running")
            sys.exit(1)

    if args.stop:
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                _kill_process(pid)
                print(f"Stopped pid {pid}")
                PID_FILE.unlink(missing_ok=True)
            except Exception as e:
                print(f"Stop failed: {e}")
                sys.exit(1)
        else:
            print("Not running (no PID file)")
        sys.exit(0)

    # Check for existing instance
    health = check_running()
    if health:
        log.info(
            "Rack server already running (pid=%s, uptime=%ss)",
            health.get("pid"),
            health.get("uptime_s"),
        )
        sys.exit(0)

    # Start server
    _write_pid()
    _ensure_dirs()
    _load_agents()
    log.info("Rack server starting on port %d", args.port)

    def _shutdown(signum, frame):
        log.info("Received signal %d, shutting down", signum)
        # Broadcast shutdown to all agents
        _broadcast(json.dumps({"type": "platform_shutdown", "ts": _ts()}))
        _remove_pid()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    # SIGTERM exists on Windows but only fires for some termination paths;
    # register it anyway — harmless if never invoked.
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (AttributeError, ValueError):
        pass

    ssl_cert = os.environ.get("IGOR_SSL_CERT", "")
    ssl_key = os.environ.get("IGOR_SSL_KEY", "")

    # Bootstrap a locally-trusted cert via mkcert if none configured or files
    # are missing. Falls back to plain HTTP if mkcert isn't installed.
    if not (
        ssl_cert and ssl_key and os.path.exists(ssl_cert) and os.path.exists(ssl_key)
    ):
        bootstrapped = _bootstrap_mkcert()
        if bootstrapped:
            ssl_cert, ssl_key = bootstrapped
            log.info("mkcert bootstrap: using %s", ssl_cert)
        else:
            log.warning(
                "No SSL cert configured and mkcert bootstrap unavailable — serving plain HTTP"
            )

    app = _make_app()
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=args.port,
        log_level="warning",
        ssl_certfile=ssl_cert if ssl_cert else None,
        ssl_keyfile=ssl_key if ssl_key else None,
    )
    server = uvicorn.Server(config)

    # When SSL is active, also serve plain HTTP on port+1 for LAN access
    # without cert warnings (same pattern as Igor's server.py)
    if ssl_cert and ssl_key:
        http_port = int(os.environ.get("IGOR_UC_HTTP_PORT", "8082"))
        log.info("SSL active — also serving plain HTTP on port %d", http_port)

        def _run_http():
            http_app = _make_app()
            http_config = uvicorn.Config(
                http_app,
                host="0.0.0.0",
                port=http_port,
                log_level="warning",
            )
            http_server = uvicorn.Server(http_config)
            asyncio.run(http_server.serve())

        import threading

        threading.Thread(target=_run_http, daemon=True, name="uc-http-fallback").start()

    try:
        asyncio.run(server.serve())
    finally:
        _remove_pid()


# ── Fallback HTML ────────────────────────────────────────────────────────────
# T-uc-channel-tabs-redesign: channel tabs + notification checkboxes.
# Removed: dashboard, ring/surprise feeds, CC bridge pane.
# Kept: Your Name, A-/A+, message input, chat area, drag-drop, WebSocket.

_FALLBACK_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Agentic Rack Server</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: monospace; background: #1a1a2e; color: #e0e0e0;
           height: 100vh; display: flex; flex-direction: column; }
    #chat { flex: 1; overflow-y: auto; padding: 1rem;
            display: flex; flex-direction: column; gap: 0.4rem; }
    .msg { font-size: 0.95rem; line-height: 1.5; }
    .msg-user   .author { color: #7ec8e3; font-weight: bold; }
    .msg-igor   .author { color: #90ee90; font-weight: bold; }
    .msg-cc     .author { color: #ffb347; font-weight: bold; }
    .msg-system { color: #888; font-style: italic; }
    .author { margin-right: 0.4rem; }
    .ts { color: #666; font-family: monospace; margin-right: 0.3rem; font-size: 0.85rem; }
    .content { white-space: pre-wrap; }
    .md p { margin: 0.3em 0; }
    .md p:first-child { margin-top: 0; }
    .md h1, .md h2, .md h3 { color: #90ee90; margin: 0.5em 0 0.2em; font-size: 1em; }
    .md strong { color: #e8e8f0; font-weight: bold; }
    .md em { font-style: italic; color: #c8c8d8; }
    .md ul, .md ol { margin: 0.3em 0 0.3em 1.4em; padding: 0; }
    .md li { margin: 0.1em 0; }
    .md code { background: #2a2a4a; padding: 0.1em 0.3em; border-radius: 2px;
               font-family: monospace; font-size: 0.9em; color: #aaddff; }
    .md pre { background: #2a2a4a; padding: 0.6em; margin: 0.4em 0;
              overflow-x: auto; border-left: 2px solid #4a4a8a; }
    .md pre code { background: none; padding: 0; color: #cce; }
    .md hr { border: none; border-top: 1px solid #333; margin: 0.5em 0; }
    .md blockquote { border-left: 2px solid #555; margin: 0.3em 0;
                     padding-left: 0.7em; color: #aaa; }
    #conn-led { font-size: 1.1em; line-height: 1; transition: color 0.3s; color: #555;
                cursor: default; }
    #conn-led.on  { color: #4caf50; }
    #conn-led.off { color: #f44336; }
    #drop-overlay { display: none; position: fixed; inset: 0; z-index: 100;
                    background: rgba(74,74,138,0.8); align-items: center;
                    justify-content: center; font-size: 2rem; color: #fff;
                    border: 4px dashed #7ec8e3; }
    #drop-overlay.active { display: flex; }
    /* Channel tab bar */
    #channel-bar { display: flex; gap: 0; align-items: center; background: #0d0d22;
                   border-bottom: 1px solid #1a1a30; padding: 0.1rem 0.4rem; overflow-x: auto;
                   white-space: nowrap; flex-shrink: 0; }
    .channel-tab { font-family: monospace; font-size: 0.78rem; padding: 0.2rem 0.6rem;
                   cursor: pointer; color: #7ec8e3; border: 1px solid transparent;
                   border-radius: 2px 2px 0 0; background: transparent; transition: color 0.2s;
                   display: inline-flex; align-items: center; gap: 0.3rem; }
    .channel-tab:hover  { color: #ccc; }
    .channel-tab.active { color: #7ec8e3; border-color: #1a1a30; background: #1a1a2e;
                          font-weight: bold; }
    .channel-tab.has-new { color: #90ee90; }
    .channel-tab input[type="checkbox"] { accent-color: #7ec8e3; cursor: pointer;
                                           width: 12px; height: 12px; }
    #new-channel-btn { font-family: monospace; font-size: 0.82rem; padding: 0.1rem 0.5rem;
                       cursor: pointer; color: #555; background: transparent; border: none;
                       margin-left: 0.3rem; }
    #new-channel-btn:hover { color: #aaa; }
    /* Controls bar */
    #name-row { display: flex; align-items: center; gap: 0.4rem; padding: 0.2rem 0.5rem 0;
                border-top: 1px solid #333; font-size: 0.78rem; color: #888; }
    #sender-name { width: 7em; background: #1e1e30; color: #aaa; border: 1px solid #444;
                   padding: 0.2rem 0.4rem; font-family: monospace; font-size: 0.78rem; }
    #input-row { display: flex; gap: 0.5rem; padding: 0.3rem 0.5rem 0.5rem; }
    #input { flex: 1; background: #2a2a3e; color: #e0e0e0;
             border: 1px solid #555; padding: 0.5rem;
             font-family: monospace; font-size: 1rem;
             resize: vertical; min-height: 2.2em; max-height: 30vh;
             overflow-y: auto; }
    button { background: #4a4a8a; color: #fff; border: none;
             padding: 0.5rem 1rem; cursor: pointer; font-family: monospace; }
    button:hover { background: #6a6aaa; }
    #status-bar { padding: 0.2rem 1rem; background: #0a0a18;
                  font-size: 0.78rem; color: #aaa; border-top: 1px solid #1a1a30;
                  min-height: 1.4em; transition: color 0.3s; }
    #status-bar.busy { color: #7ec8e3; }
    /* Two-tab layout */
    #main-tab-bar { display: flex; background: #0d0d22; border-bottom: 2px solid #2a2a4a;
                    flex-shrink: 0; }
    .main-tab { font-family: monospace; font-size: 0.85rem; padding: 0.35rem 1.2rem;
                background: transparent; color: #888; border: none; cursor: pointer;
                border-bottom: 2px solid transparent; margin-bottom: -2px; }
    .main-tab:hover { color: #ccc; }
    .main-tab.active { color: #7ec8e3; border-bottom-color: #7ec8e3; font-weight: bold; }
    .main-panel { display: none; flex: 1; flex-direction: column; overflow: hidden; }
    .main-panel.active { display: flex; }
    /* Control station */
    #ctrl-body { flex: 1; overflow-y: auto; padding: 1.2rem; }
    .ctrl-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
                 gap: 0.8rem; margin-top: 0.8rem; }
    .ctrl-card { background: #2a2a3e; border: 1px solid #3a3a5a; border-radius: 4px;
                 padding: 0.8rem 1rem; }
    .ctrl-card h3 { color: #90ee90; font-size: 0.9rem; margin-bottom: 0.4rem; }
    .ctrl-card a { color: #7ec8e3; display: block; margin: 0.15rem 0; font-size: 0.85rem; }
    .ctrl-card p { color: #888; font-size: 0.8rem; margin-top: 0.3rem; }
  </style>
</head>
<body>
  <div id="drop-overlay">Drop file to send</div>
  <div id="main-tab-bar">
    <button class="main-tab active" data-tab="comms" onclick="switchTab('comms')">Comms</button>
    <button class="main-tab" data-tab="control" onclick="switchTab('control')">Control Station</button>
  </div>
  <div class="main-panel active" id="panel-comms">
  <div id="channel-bar">
    <span class="channel-tab active" data-channel="comms://shared" onclick="switchChannel('comms://shared')">
      Public <input type="checkbox" title="Notify on new messages" onclick="event.stopPropagation(); toggleNotify('comms://shared', this)">
    </span>
    <button id="new-channel-btn" onclick="newChannel()" title="New channel">+</button>
  </div>
  <div id="chat"></div>
  <div id="status-bar">idle</div>
  <div id="name-row">
    <span id="conn-led" title="Connection status">*</span>
    <label for="sender-name">Your name:</label>
    <input id="sender-name" type="text" value="akien" maxlength="32" autocomplete="off">
    <button onclick="changeFontSize(-1)" title="Decrease font size" style="padding:0.2rem 0.5rem;font-size:0.85rem;">A-</button>
    <button onclick="changeFontSize(1)" title="Increase font size" style="padding:0.2rem 0.5rem;font-size:0.85rem;">A+</button>
  </div>
  <div id="input-row">
    <textarea id="input" placeholder="Message the channel..." autocomplete="off" rows="4"></textarea>
    <button onclick="sendMsg()">Send</button>
    <button onclick="document.getElementById('file-input').click()">clip</button>
    <input id="file-input" type="file" style="display:none" onchange="uploadFile(this)">
  </div>
  </div><!-- /panel-comms -->
  <div class="main-panel" id="panel-control">
    <div id="ctrl-body">
      <h2 style="color:#7ec8e3;font-size:1rem;margin-bottom:.8rem">Control Station</h2>
      <div class="ctrl-grid">
        <div class="ctrl-card">
          <h3>Rack Health</h3>
          <a href="/rack">Device status &amp; OR budget</a>
          <p>All registered machines, heartbeats, and burn rate.</p>
        </div>
        <div class="ctrl-card">
          <h3>Goals</h3>
          <a href="/goals">Goals tree</a>
          <p>Akien&apos;s goals &amp; values (palace.shared.akien.goals).</p>
        </div>
        <div class="ctrl-card">
          <h3>Decisions</h3>
          <a href="/decisions">All decisions</a>
          <p>Design decisions (D-xxx) and their spawned tickets.</p>
        </div>
        <div class="ctrl-card">
          <h3>Questions</h3>
          <a href="/questions">Open questions</a>
          <p>Unresolved questions filed during design sessions.</p>
        </div>
        <div class="ctrl-card">
          <h3>Hypotheses</h3>
          <a href="/hypotheses">Hypotheses</a>
          <p>Testable claims filed with decisions.</p>
        </div>
        <div class="ctrl-card">
          <h3>Outcomes</h3>
          <a href="/outcomes">Outcomes</a>
          <p>Post-ship outcome records — did the hypothesis hold?</p>
        </div>
        <div class="ctrl-card">
          <h3>Palace Browser</h3>
          <a href="/palace">Browse all palace nodes</a>
          <p>Full adc.palace tree — goals, sessions, decisions, rules.</p>
        </div>
        <div class="ctrl-card">
          <h3>Tools</h3>
          <a href="/dashboard">System dashboard</a>
          <a href="/metrics">Metrics</a>
        </div>
      </div>
    </div>
  </div><!-- /panel-control -->
  <script>
    const chat       = document.getElementById('chat');
    const input      = document.getElementById('input');
    const senderName = document.getElementById('sender-name');
    const status     = document.getElementById('status-bar');
    const overlay    = document.getElementById('drop-overlay');
    const channelBar = document.getElementById('channel-bar');
    let ws, dragDepth = 0;
    const _knownAgents = new Set();
    let currentChannel = 'comms://shared';
    const channelMsgs = {'comms://shared': []};
    const channelNotify = {};  // channel -> bool (notification checkbox state)

    // ── Main tab switch ──
    function switchTab(name) {
      document.querySelectorAll('.main-panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.main-tab').forEach(t => t.classList.remove('active'));
      document.getElementById('panel-' + name).classList.add('active');
      document.querySelector('.main-tab[data-tab="' + name + '"]').classList.add('active');
    }

    // ── Name persistence ──
    function _saveName(n) {
      localStorage.setItem('igor_sender_name', n);
      document.cookie = 'igor_user=' + encodeURIComponent(n) + '; path=/; max-age=31536000; SameSite=Lax';
    }
    function _loadName() {
      const _ck = document.cookie.split(';').map(c => c.trim())
        .find(c => c.startsWith('igor_user='));
      if (_ck) return decodeURIComponent(_ck.split('=')[1]);
      return localStorage.getItem('igor_sender_name') || '';
    }
    const _savedName = _loadName();
    if (_savedName) senderName.value = _savedName;
    senderName.addEventListener('change', () => _saveName(senderName.value));

    // ── Font size ──
    let _fontSize = parseFloat(localStorage.getItem('igor_font_size') || '0.95');
    function _applyFontSize() { chat.style.fontSize = _fontSize + 'rem'; }
    function changeFontSize(delta) {
      _fontSize = Math.min(Math.max(_fontSize + delta * 0.1, 0.6), 2.0);
      _fontSize = Math.round(_fontSize * 100) / 100;
      localStorage.setItem('igor_font_size', String(_fontSize));
      _applyFontSize();
    }
    _applyFontSize();

    // ── Channel tab bar ──
    function _renderChannelBar() {
      const existing = new Set([...channelBar.querySelectorAll('.channel-tab')].map(t => t.dataset.channel));
      Object.keys(channelMsgs).forEach(ch => {
        if (!existing.has(ch)) {
          const tab = document.createElement('span');
          tab.className = 'channel-tab'; tab.dataset.channel = ch;
          const label = ch.replace('comms://', '');
          const checked = channelNotify[ch] ? 'checked' : '';
          tab.innerHTML = label + ' <input type="checkbox" title="Notify" ' + checked +
            ' onclick="event.stopPropagation(); toggleNotify(\'' + ch + '\', this)">';
          tab.onclick = () => switchChannel(ch);
          channelBar.insertBefore(tab, document.getElementById('new-channel-btn'));
        }
      });
      channelBar.querySelectorAll('.channel-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.channel === currentChannel);
        if (t.dataset.channel === currentChannel) t.classList.remove('has-new');
      });
    }

    function _renderChannel(ch) {
      chat.innerHTML = '';
      (channelMsgs[ch] || []).forEach(m => {
        const cls = _knownAgents.has(m.author) ? 'igor' : m.author === 'claude-code' ? 'cc' : 'user';
        const label = _knownAgents.has(m.author) ? m.author : m.author === 'claude-code' ? 'CC>' : (m.author || 'You');
        addMsg(cls, label, m.content, m.ts);
      });
    }

    function _hhmmss(ts) {
      if (!ts) return '';
      const m = /(\d{2}):(\d{2}):(\d{2})/.exec(ts);
      return m ? m[1] + m[2] + m[3] : '';
    }

    function switchChannel(ch) {
      if (!channelMsgs[ch]) channelMsgs[ch] = [];
      currentChannel = ch;
      _renderChannelBar(); _renderChannel(ch);
      if (ws && ws.readyState === 1)
        ws.send(JSON.stringify({type: 'join_session', session_id: ch}));
    }

    function newChannel() {
      const name = prompt('Channel name (e.g. debug, notes):');
      if (name === null || !name.trim()) return;
      const ch = 'comms://' + name.trim().toLowerCase();
      if (!channelMsgs[ch]) channelMsgs[ch] = [];
      switchChannel(ch);
    }

    function toggleNotify(ch, checkbox) {
      channelNotify[ch] = checkbox.checked;
      localStorage.setItem('channel_notify', JSON.stringify(channelNotify));
    }

    // Load saved notification preferences
    try {
      const saved = JSON.parse(localStorage.getItem('channel_notify') || '{}');
      Object.assign(channelNotify, saved);
    } catch(e) {}

    // ── Markdown ──
    function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

    function parseMarkdown(raw) {
      function fmt(s) {
        s = s.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
        s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
        return s;
      }
      const lines = raw.split('\n');
      const out = [];
      let inCode = false, codeLines = [];
      let inUl = false, inOl = false;
      let paraLines = [];
      function flushPara() { if (!paraLines.length) return; out.push('<p>' + paraLines.join('<br>') + '</p>'); paraLines = []; }
      function flushList() { if (inUl) { out.push('</ul>'); inUl = false; } if (inOl) { out.push('</ol>'); inOl = false; } }
      for (const line of lines) {
        if (line.startsWith('```')) {
          if (inCode) { out.push('<pre><code>' + esc(codeLines.join('\n')) + '</code></pre>'); codeLines = []; inCode = false; }
          else { flushPara(); flushList(); inCode = true; }
          continue;
        }
        if (inCode) { codeLines.push(line); continue; }
        if (!line.trim()) { flushPara(); flushList(); continue; }
        const hm = line.match(/^(#{1,3}) (.+)$/);
        if (hm) { flushPara(); flushList(); const lv = hm[1].length; out.push('<h'+lv+'>'+fmt(esc(hm[2]))+'</h'+lv+'>'); continue; }
        if (/^---+$/.test(line)) { flushPara(); flushList(); out.push('<hr>'); continue; }
        const bq = line.match(/^> (.+)$/);
        if (bq) { flushPara(); flushList(); out.push('<blockquote>'+fmt(esc(bq[1]))+'</blockquote>'); continue; }
        const ul = line.match(/^[ \t]*[-*] (.+)$/);
        if (ul) { flushPara(); if (!inUl) { flushList(); out.push('<ul>'); inUl=true; } out.push('<li>'+fmt(esc(ul[1]))+'</li>'); continue; }
        const ol = line.match(/^\d+\. (.+)$/);
        if (ol) { flushPara(); if (!inOl) { flushList(); out.push('<ol>'); inOl=true; } out.push('<li>'+fmt(esc(ol[1]))+'</li>'); continue; }
        flushList(); paraLines.push(fmt(esc(line)));
      }
      flushPara(); flushList();
      if (inCode) out.push('<pre><code>' + esc(codeLines.join('\n')) + '</code></pre>');
      return out.join('\n');
    }

    function addMsg(cls, author, content, ts) {
      const d = document.createElement('div');
      d.className = 'msg msg-' + cls;
      const hhmmss = _hhmmss(ts);
      if (hhmmss) { const t = document.createElement('span'); t.className='ts'; t.textContent=hhmmss+' '; d.appendChild(t); }
      if (author) { const s = document.createElement('span'); s.className='author'; s.textContent=author+':'; d.appendChild(s); }
      const c = document.createElement(cls === 'igor' ? 'div' : 'span');
      if (cls === 'igor') { c.className='content md'; c.innerHTML=parseMarkdown(content); }
      else { c.className='content'; c.textContent=content; }
      d.appendChild(c); chat.appendChild(d); chat.scrollTop = chat.scrollHeight;
    }

    // ── WebSocket ──
    const led = document.getElementById('conn-led');
    let _connectedOnce = false, _disconnectedMsgShown = false, _retryDelay = 2000;
    function setLed(on) { led.classList.toggle('on',on); led.classList.toggle('off',!on); led.title = on ? 'Connected' : 'Disconnected'; }

    function connect() {
      ws = new WebSocket((location.protocol==='https:'?'wss://':'ws://') + location.host + '/ws');
      ws.onopen = () => {
        setLed(true); _retryDelay = 2000;
        if (!_connectedOnce) { addMsg('system','','Connected to Agentic Rack Server.'); _connectedOnce=true; }
        else { addMsg('system','','Reconnected.'); }
        _disconnectedMsgShown = false;
        const _cookieName = _loadName();
        if (_cookieName) ws.send(JSON.stringify({type:'identify', name:_cookieName}));
        ws.send(JSON.stringify({type:'join_session', session_id:currentChannel}));
      };
      ws.onerror = () => { ws.close(); };
      ws.onclose = () => {
        setLed(false);
        if (!_disconnectedMsgShown) { addMsg('system','','Disconnected. Retrying...'); _disconnectedMsgShown=true; }
        setTimeout(connect, _retryDelay); _retryDelay = Math.min(_retryDelay*2, 30000);
      };
      ws.onmessage = e => {
        const m = JSON.parse(e.data);
        if (m.type === 'message') {
          const ch = m.session_id || 'comms://shared';
          if (!channelMsgs[ch]) channelMsgs[ch] = [];
          channelMsgs[ch].push(m);
          if (channelMsgs[ch].length > 50) channelMsgs[ch].shift();
          _renderChannelBar();
          if (ch === currentChannel) {
            const cls = _knownAgents.has(m.author) ? 'igor' : m.author === 'claude-code' ? 'cc' : 'user';
            const label = _knownAgents.has(m.author) ? m.author : m.author === 'claude-code' ? 'CC>' : (m.author || 'You');
            addMsg(cls, label, m.content, m.ts);
          } else {
            // Mark tab as having new messages (blue -> green)
            const tab = channelBar.querySelector('[data-channel="'+ch+'"]');
            if (tab) tab.classList.add('has-new');
          }
        } else if (m.type === 'session_history') {
          const ch = m.session_id || 'comms://shared';
          channelMsgs[ch] = m.messages || [];
          _renderChannelBar();
          if (ch === currentChannel) _renderChannel(ch);
        } else if (m.type === 'file_dropped')
          addMsg('system','','clip ' + m.filename + ' received in inbox');
        else if (m.type === 'activity') {
          const busy = m.busy === true;
          status.className = busy ? 'busy' : '';
          status.textContent = (busy ? '* ' : '  ') + (m.action || (busy ? 'processing' : 'idle'));
        } else if (m.type === 'agent_status') {
          if (m.status === 'attached') {
            _knownAgents.add(m.agent_id);
            // Auto-create channel tab for new agent
            const agentCh = 'comms://' + m.agent_id;
            if (!channelMsgs[agentCh]) { channelMsgs[agentCh] = []; channelNotify[agentCh] = true; }
            _renderChannelBar();
          } else { _knownAgents.delete(m.agent_id); }
          addMsg('system','', m.agent_id + ' ' + m.status);
        } else if (m.type === 'platform_status') {
          const aa = m.attached_agents || [];
          _knownAgents.clear(); aa.forEach(a => {
            _knownAgents.add(a);
            const agentCh = 'comms://' + a;
            if (!channelMsgs[agentCh]) { channelMsgs[agentCh] = []; channelNotify[agentCh] = true; }
          });
          _renderChannelBar();
        } else if (m.type === 'name_resolved') {
          senderName.value = m.name; _saveName(m.name);
        }
      };
    }

    // ── Send ──
    function sendMsg() {
      const rawText = input.value.trim();
      if (!rawText || !ws || ws.readyState !== 1) return;
      const name = (senderName.value.trim() || 'akien').toLowerCase();
      ws.send(JSON.stringify({type:'message', content:rawText, author:name, session_id:currentChannel}));
      input.value = '';
    }
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(); }
    });

    // ── File upload ──
    async function uploadFile(el) {
      const file = el.files[0]; if (!file) return;
      const fd = new FormData(); fd.append('file', file);
      const r = await fetch('/api/upload', {method:'POST', body:fd});
      const j = await r.json();
      addMsg('system','','clip ' + j.filename + ' uploaded to inbox');
      el.value = '';
    }

    // ── Drag and drop ──
    document.addEventListener('dragenter', e => {
      if (e.dataTransfer.types.includes('Files')) { dragDepth++; overlay.classList.add('active'); }
    });
    document.addEventListener('dragleave', () => {
      if (--dragDepth <= 0) { dragDepth=0; overlay.classList.remove('active'); }
    });
    document.addEventListener('dragover', e => e.preventDefault());
    document.addEventListener('drop', async e => {
      e.preventDefault(); dragDepth=0; overlay.classList.remove('active');
      const file = e.dataTransfer.files[0]; if (!file) return;
      const fd = new FormData(); fd.append('file', file);
      const r = await fetch('/api/upload', {method:'POST', body:fd});
      const j = await r.json();
      addMsg('system','','clip ' + j.filename + ' dropped into inbox');
    });

    // ── Fetch channel list from comms API ──
    async function loadChannels() {
      try {
        const r = await fetch('/api/comms/channels');
        const d = await r.json();
        (d.channels || []).forEach(ch => {
          if (!channelMsgs[ch.address]) channelMsgs[ch.address] = [];
          if (ch.notify && !(ch.address in channelNotify)) channelNotify[ch.address] = true;
        });
        _renderChannelBar();
      } catch(e) {}
    }

    connect();
    loadChannels();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()

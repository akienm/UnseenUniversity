import logging

"""
Web server for Igor — Starlette/uvicorn, runs in a background daemon thread.

Endpoints:
  GET  /                    → serve web_ui/dist/index.html (fallback HTML if not built)
  GET  /assets/{path}       → serve web_ui/dist/assets/
  WS   /ws                  → WebSocket chat (bidirectional)
  POST /api/upload          → save file to inbox, notify Igor
  POST /api/cc_send         → Claude Code → Igor channel (author: "claude-code")
  GET  /api/outbox          → JSON list of outbox files with size/mtime
  GET  /api/outbox/{file}   → download file from outbox
  GET  /api/dashboard       → JSON stats snapshot
  POST /api/bridge_chat     → proxy to claude_bridge on 8082

WebSocket message protocol (JSON):
  Client → server: {"type": "message", "content": "hello igor"}
  Server → client: {"type": "message", "author": "igor"|"user", "content": "...", "ts": "..."}
  Server → client: {"type": "file_dropped", "filename": "...", "ts": "..."}
  Server → client: {"type": "dashboard", ...stats...}

Thread-safe queues:
  incoming  — web messages → Igor (drained by listener._poll_web)
  (outgoing is handled by direct broadcast via send())

Port: IGOR_WEB_PORT env var, default 8080.
"""

import asyncio
import json
import os
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ..paths import paths
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

# ── Paths ──────────────────────────────────────────────────────────────────────
_INSTANCE_DIR = paths().instance
INBOX_DIR = _INSTANCE_DIR / "inbox"
OUTBOX_DIR = _INSTANCE_DIR / "outbox"
_DIST_DIR = Path(__file__).parent.parent.parent.parent / "web_ui" / "dist"

# ── Thread-safe queue: web messages → Igor ────────────────────────────────────
incoming: queue.Queue = queue.Queue()

# ── Per-session asyncio queues for broadcast (#119) ──────────────────────────
_session_clients: dict = {}  # session_id → [asyncio.Queue, ...]
_client_session: dict = {}  # id(ws) → session_id
_session_history: dict = {}  # session_id → [{...}, ...] (capped at 50)
_client_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None

# ── Shared state ──────────────────────────────────────────────────────────────
_stats_fn = (
    None  # callable → dict; set by start(); Igor class owns all state (change.30)
)
_cortex_fn = None  # callable → Cortex; set by start(); used by /api/cc_notebook (#239)
_igor_fn = None  # callable → Igor;   set by start(); used by /api/execute_habit (D094)

# ── Shared channel mirror ─────────────────────────────────────────────────────
# Append messages to ~/.TheIgors/cc_channel/messages.jsonl so all CC sessions
# and Igor share a single visible discussion channel (no Igor required to read).

_CHANNEL_FILE = paths().cc_channel / "messages.jsonl"


def _channel_append(author: str, content: str, msg_type: str = "message"):
    """Mirror a message to the shared JSONL channel and Postgres. Never raises."""
    try:
        _CHANNEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        from datetime import timezone

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {"ts": ts, "author": author, "type": msg_type, "content": content}
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(_CHANNEL_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        # Mirror to Postgres channel_messages so MCP channel_read sees Igor replies
        _pg_url = os.environ.get("IGOR_HOME_DB_URL", "") or os.environ.get("IGOR_DB_URL", "")
        if _pg_url:
            try:
                import psycopg2
                conn = psycopg2.connect(_pg_url)
                with conn:
                    with conn.cursor() as c:
                        c.execute(
                            "INSERT INTO channel_messages (ts, author, type, content) "
                            "VALUES (%s, %s, %s, %s)",
                            (ts, author, msg_type, content),
                        )
                conn.close()
            except Exception as _pg_e:
                logging.getLogger(__name__).debug(
                    "channel_append PG write failed (non-fatal): %s", _pg_e
                )
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/web/server.py: %s", _bare_e
        )


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_dirs():
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)


# ── Public API ────────────────────────────────────────────────────────────────


def send(text: str, session_id: str = "shared"):
    """Send an Igor response to a specific session's clients. Thread-safe."""
    msg = {
        "type": "message",
        "author": "igor",
        "content": text,
        "ts": _ts(),
        "session_id": session_id,
    }
    _add_to_history(session_id, msg)
    _broadcast_to_session(session_id, json.dumps(msg))
    _channel_append("igor", text)


def broadcast_activity(state: dict):
    """Broadcast a live activity state update to all connected WebSocket clients.

    state dict keys (all optional):
        action  — human-readable description of what Igor is doing right now
        tier    — current reasoning tier ("tier.2", "tier.3", etc.) or ""
        input   — first 60 chars of the current user input
        busy    — bool: True while processing, False when idle
    """
    _broadcast(
        json.dumps(
            {
                "type": "activity",
                "ts": _ts(),
                **state,
            }
        )
    )


def broadcast_name_resolved(name: str):
    """Tell the web client to update its sender-name input box."""
    _broadcast(json.dumps({"type": "name_resolved", "name": name, "ts": _ts()}))


def _add_to_history(session_id: str, msg: dict):
    """Add a message to session history (capped at 50)."""
    with _client_lock:
        hist = _session_history.setdefault(session_id, [])
        hist.append(msg)
        if len(hist) > 50:
            hist.pop(0)


def _broadcast_to_session(session_id: str, payload: str):
    """Fan out a payload to clients in a specific session."""
    if _loop is None:
        return
    with _client_lock:
        queues = list(_session_clients.get(session_id, []))
    for q in queues:
        _loop.call_soon_threadsafe(q.put_nowait, payload)


def _broadcast(payload: str):
    """Fan out a JSON payload to every connected WebSocket client (all sessions)."""
    if _loop is None:
        return
    with _client_lock:
        all_queues = [q for qs in _session_clients.values() for q in qs]
    for q in all_queues:
        _loop.call_soon_threadsafe(q.put_nowait, payload)


# ── Route handlers ────────────────────────────────────────────────────────────


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
    safe_name = Path(file.filename).name  # strip any path prefix — no traversal
    dest = INBOX_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)
    # Notify Igor via listener queue
    incoming.put(
        {
            "content": f"[File uploaded: {safe_name}]",
            "filename": safe_name,
            "author": "web-user",
        }
    )
    # Tell all WebSocket clients a file arrived
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
    except OSError as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/web/server.py: %s", _bare_e
        )
    return JSONResponse(files)


async def _api_outbox_download(request: Request):
    safe = Path(request.path_params["filename"]).name  # no path traversal
    path = OUTBOX_DIR / safe
    if not path.exists():
        return Response("Not found", status_code=404)
    return FileResponse(str(path), filename=safe)


async def _api_cc_send(request: Request):
    """CC→Igor channel: Claude Code injects a message with author 'claude-code'."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "empty content"}, status_code=400)
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
    """GET /api/health — simple liveness check; always returns 200 if server is up."""
    return JSONResponse({"status": "ok"})


async def _api_cc_notebook(request: Request):
    """
    #239: Employer notebook endpoint for Claude (and any employer).

    GET  /api/cc_notebook?employer=claude  — return notebook entries for employer
    POST /api/cc_notebook                  — add a notebook entry
         body: {"employer": "claude", "key": "...", "content": "...", "parent_id": "CP2"}

    Notebook entries are FACTUAL memories tagged with metadata.employer_id.
    No schema change — convention only.
    """
    if _cortex_fn is None:
        return JSONResponse({"error": "cortex not available"}, status_code=503)

    cortex = _cortex_fn()
    if cortex is None:
        return JSONResponse({"error": "cortex not available"}, status_code=503)

    if request.method == "GET":
        employer = request.query_params.get("employer", "claude")
        try:
            entries = cortex.for_employer(employer)
            return JSONResponse(
                {
                    "employer": employer,
                    "count": len(entries),
                    "entries": [
                        {
                            "id": m.id,
                            "narrative": m.narrative,
                            "memory_type": m.memory_type.value,
                            "metadata": m.metadata,
                        }
                        for m in entries
                    ],
                }
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # POST — add entry
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    employer = body.get("employer", "claude")
    key = body.get("key", "").strip()
    content = body.get("content", "").strip()
    if not key or not content:
        return JSONResponse({"error": "key and content required"}, status_code=400)

    try:
        from ..memory.models import Memory, MemoryType

        mem = Memory(
            id=f"NB_{employer.upper()}_{key.upper().replace(' ', '_')[:40]}",
            narrative=content,
            memory_type=MemoryType.FACTUAL,
            parent_id=body.get("parent_id", "CP2"),
            metadata={
                "employer_id": employer,
                "notebook_key": key,
                "source": "cc_notebook",
            },
        )
        existing = cortex.get(mem.id)
        if existing:
            # Update in place via store (upsert by id)
            cortex.store(mem)
            return JSONResponse({"status": "updated", "id": mem.id})
        cortex.store(mem)
        return JSONResponse({"status": "created", "id": mem.id})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _api_dashboard(request: Request):
    data: dict = {}
    if _stats_fn is not None:
        try:
            data = dict(_stats_fn())
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/web/server.py: %s", _bare_e
            )
    data["ts"] = _ts()
    return JSONResponse(data)


async def _ws_endpoint(ws: WebSocket):
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    current_session = "shared"
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
                    # Client re-identifying from cookie on (re)connect.
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
                    # Client switching to a named session (or creating it)
                    new_sid = (msg.get("session_id") or "shared").strip()[
                        :64
                    ] or "shared"
                    with _client_lock:
                        # Leave old session
                        old_qs = _session_clients.get(current_session, [])
                        if q in old_qs:
                            old_qs.remove(q)
                        # Join new session
                        _session_clients.setdefault(new_sid, []).append(q)
                        _client_session[id(ws)] = new_sid
                        hist = list(_session_history.get(new_sid, []))
                    current_session = new_sid
                    # Send history for the new session
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
                        incoming.put(
                            {
                                "content": content,
                                "author": author,
                                "client_id": id(ws),
                                "session_id": current_session,
                            }
                        )
                        # Echo user message to session clients only
                        umsg = {
                            "type": "message",
                            "author": author,
                            "content": content,
                            "ts": _ts(),
                            "session_id": current_session,
                        }
                        _add_to_history(current_session, umsg)
                        _broadcast_to_session(current_session, json.dumps(umsg))
                        _channel_append(author, content)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/web/server.py: %s", _bare_e
            )

    async def _forward():
        try:
            while True:
                payload = await q.get()
                await ws.send_text(payload)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/web/server.py: %s", _bare_e
            )

    recv = asyncio.ensure_future(_receive())
    fwd = asyncio.ensure_future(_forward())
    await asyncio.wait([recv, fwd], return_when=asyncio.FIRST_COMPLETED)
    for t in (recv, fwd):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/web/server.py: %s", _bare_e
            )

    with _client_lock:
        qs = _session_clients.get(current_session, [])
        if q in qs:
            qs.remove(q)
        _client_session.pop(id(ws), None)


# ── #119: Sessions API ────────────────────────────────────────────────────────


async def _api_sessions(request: Request):
    """GET /api/sessions — list active sessions and their client counts."""
    with _client_lock:
        sessions = {sid: len(qs) for sid, qs in _session_clients.items() if qs}
    return JSONResponse({"sessions": sessions})


# ── G16: Global milieu API endpoints ─────────────────────────────────────────


async def _api_milieu_global(request):
    """GET /api/milieu/global — serve current global milieu state (cross-machine sync)."""
    from pathlib import Path as _Path
    import json as _j

    _gpath = paths().milieu
    try:
        data = _j.loads(_gpath.read_text(encoding="utf-8")) if _gpath.exists() else {}
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _api_milieu_contribute(request):
    """POST /api/milieu/contribute — accept a milieu contribution from a remote instance."""
    from pathlib import Path as _Path

    try:
        body = await request.json()
        from ..cognition.milieu import (
            MilieuState,
            GLOBAL_ALPHA_SPIKE,
            _contribute_to_global,
        )
        from dataclasses import fields as _fields

        state = MilieuState(
            **{k: body[k] for k in body if k in {f.name for f in _fields(MilieuState)}}
        )
        _contribute_to_global(state, GLOBAL_ALPHA_SPIKE)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ── D094: Direct habit execution ─────────────────────────────────────────────


async def _api_execute_habit(request: Request):
    """
    POST /api/execute_habit
    Body: {"habit_id": "PROC_WHAT_TIME", "args": {}}

    Execute an Igor habit directly by ID, bypassing NLU/thalamus.
    Returns: {status, result, habit_id, habit_type, duration_ms}
    Every call is logged to ~/.TheIgors/logs/cc_session_YYYYMMDD.log.
    """
    if _igor_fn is None:
        return JSONResponse({"error": "igor not available"}, status_code=503)
    igor = _igor_fn()
    if igor is None:
        return JSONResponse({"error": "igor not available"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    habit_id = (body.get("habit_id") or "").strip()
    if not habit_id:
        return JSONResponse({"error": "habit_id required"}, status_code=400)

    args = body.get("args") or {}
    if not isinstance(args, dict):
        return JSONResponse({"error": "args must be a JSON object"}, status_code=400)

    try:
        result = igor.execute_habit(habit_id, args)
        status_code = 200 if result.get("status") == "ok" else 422
        return JSONResponse(result, status_code=status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _api_execute_habit_get(request: Request):
    """
    GET /api/execute_habit/{habit_id}
    Discoverability: returns habit narrative + metadata so Claude can inspect
    what a habit does before deciding to call it.
    """
    if _igor_fn is None:
        return JSONResponse({"error": "igor not available"}, status_code=503)
    igor = _igor_fn()
    if igor is None:
        return JSONResponse({"error": "igor not available"}, status_code=503)

    habit_id = request.path_params.get("habit_id", "").strip()
    if not habit_id:
        return JSONResponse({"error": "habit_id required"}, status_code=400)

    try:
        habit = igor.cortex.get(habit_id)
        if habit is None:
            return JSONResponse(
                {"error": f"habit '{habit_id}' not found"}, status_code=404
            )
        return JSONResponse(
            {
                "habit_id": habit.id,
                "narrative": habit.narrative,
                "habit_type": habit.metadata.get("habit_type", "action"),
                "trigger": habit.metadata.get("trigger", ""),
                "code_ref": habit.metadata.get("code_ref", ""),
                "action": habit.metadata.get("action", ""),
            }
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── D105: Claude bridge proxy ─────────────────────────────────────────────────

_BRIDGE_PORT = int(os.getenv("CLAUDE_BRIDGE_PORT", "8082"))


async def _api_bridge_chat(request: Request) -> JSONResponse:
    """POST /api/bridge_chat — proxy to claude_bridge on _BRIDGE_PORT.

    Body: {"message": "...", "channel": "shared|back"}
    Forwards to http://localhost:8082/chat and returns the response.
    Allows the web UI to call Claude without CORS issues.
    """
    try:
        body_bytes = await request.body()
    except Exception:
        return JSONResponse({"error": "read error"}, status_code=400)

    import urllib.request as _ur
    import urllib.error as _ue

    def _do_proxy():
        req = _ur.Request(
            f"http://localhost:{_BRIDGE_PORT}/chat",
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=90) as resp:
            return resp.read()

    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _do_proxy)
        return JSONResponse(json.loads(data))
    except _ue.URLError:
        return JSONResponse(
            {
                "error": "Claude bridge unavailable — is claude_bridge.py running on port 8082?"
            },
            status_code=503,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Starlette app factory ─────────────────────────────────────────────────────


def _make_app() -> Starlette:
    async def on_startup():
        global _loop
        _loop = asyncio.get_running_loop()

    routes = [
        Route("/", _index),
        WebSocketRoute("/ws", _ws_endpoint),
        Route("/api/upload", _api_upload, methods=["POST"]),
        Route("/api/cc_send", _api_cc_send, methods=["POST"]),
        Route("/api/outbox", _api_outbox_list),
        Route("/api/outbox/{filename}", _api_outbox_download),
        Route("/api/health", _api_health),
        Route("/api/dashboard", _api_dashboard),
        Route("/api/sessions", _api_sessions),
        Route("/api/milieu/global", _api_milieu_global),
        Route("/api/milieu/contribute", _api_milieu_contribute, methods=["POST"]),
        Route("/api/cc_notebook", _api_cc_notebook, methods=["GET", "POST"]),
        Route("/api/execute_habit", _api_execute_habit, methods=["POST"]),
        Route("/api/execute_habit/{habit_id}", _api_execute_habit_get, methods=["GET"]),
        Route("/api/bridge_chat", _api_bridge_chat, methods=["POST"]),
    ]

    # Serve compiled Svelte assets if the UI has been built
    assets_dir = _DIST_DIR / "assets"
    if assets_dir.exists():
        routes.append(
            Mount("/assets", app=StaticFiles(directory=str(assets_dir)), name="assets")
        )

    return Starlette(routes=routes, on_startup=[on_startup])


# ── Server lifecycle ──────────────────────────────────────────────────────────

_server_thread: Optional[threading.Thread] = None


def start(stats_fn=None, cortex_fn=None, igor_fn=None):
    """Start the web server in a background daemon thread. Non-blocking.

    stats_fn:  callable () → dict    — Igor.get_stats(); called by /api/dashboard.
    cortex_fn: callable () → Cortex  — Igor.get_cortex(); used by /api/cc_notebook (#239).
    igor_fn:   callable () → Igor    — Igor instance; used by /api/execute_habit (D094).
    Igor owns all state; web server owns none (change.30 gateway pattern).
    """
    global _server_thread, _stats_fn, _cortex_fn, _igor_fn
    _stats_fn = stats_fn
    _cortex_fn = cortex_fn
    _igor_fn = igor_fn
    _ensure_dirs()

    if _server_thread and _server_thread.is_alive():
        return

    port = int(os.getenv("IGOR_WEB_PORT", "8080"))
    ssl_cert = os.getenv("IGOR_SSL_CERT", "")
    ssl_key = os.getenv("IGOR_SSL_KEY", "")

    def _run():
        app = _make_app()
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            log_level="warning",
            ssl_certfile=ssl_cert if ssl_cert else None,
            ssl_keyfile=ssl_key if ssl_key else None,
        )
        server = uvicorn.Server(config)
        asyncio.run(server.serve())

    _server_thread = threading.Thread(target=_run, daemon=True, name="web-server")
    _server_thread.start()

    # When SSL is active, also serve plain HTTP on port+1 for LAN access.
    # e.g. http://10.0.0.229:8081/ works without cert warnings.
    if ssl_cert and ssl_key:
        http_port = int(os.getenv("IGOR_HTTP_PORT", str(port + 1)))

        def _run_http():
            app = _make_app()
            config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=http_port,
                log_level="warning",
            )
            server = uvicorn.Server(config)
            asyncio.run(server.serve())

        threading.Thread(target=_run_http, daemon=True, name="web-server-http").start()


def is_running() -> bool:
    return _server_thread is not None and _server_thread.is_alive()


# ── Fallback HTML (when Svelte UI not yet built) ───────────────────────────────
# Fully functional single-page chat UI — no npm required to use Igor via browser.

_FALLBACK_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Igor</title>
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
    .content { white-space: pre-wrap; }
    /* Markdown rendering for Igor messages */
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
    #dashboard { padding: 0.3rem 1rem; background: #0f0f1e;
                 font-size: 0.8rem; color: #888; border-top: 1px solid #222;
                 display: flex; gap: 1rem; }
    #ring-feed { max-height: 0; overflow: hidden; transition: max-height 0.3s ease;
                 background: #080814; border-top: 1px solid #1a1a30; }
    #ring-feed.open { max-height: 14em; overflow-y: auto; }
    #ring-feed table { width: 100%; border-collapse: collapse; font-size: 0.73rem;
                       font-family: monospace; color: #99a; }
    #ring-feed td { padding: 0.15rem 0.5rem; border-bottom: 1px solid #111; vertical-align: top; }
    #ring-feed td.cat { color: #7ec8e3; white-space: nowrap; width: 12em; }
    #ring-toggle { cursor: pointer; user-select: none; padding: 0 0.4rem; color: #555;
                   font-size: 0.85em; }
    #ring-toggle:hover { color: #aaa; }
    #surprise-feed { max-height: 0; overflow: hidden; transition: max-height 0.3s ease;
                     background: #080814; border-top: 1px solid #1a1a30; }
    #surprise-feed.open { max-height: 10em; overflow-y: auto; }
    #surprise-feed table { width: 100%; border-collapse: collapse; font-size: 0.73rem;
                           font-family: monospace; color: #99a; }
    #surprise-feed td { padding: 0.15rem 0.5rem; border-bottom: 1px solid #111; vertical-align: top; }
    #surprise-toggle { cursor: pointer; user-select: none; padding: 0 0.4rem; color: #555;
                       font-size: 0.85em; }
    #surprise-toggle:hover { color: #aaa; }
    #surprise-avg.low  { color: #5c5; }
    #surprise-avg.mid  { color: #cc5; }
    #surprise-avg.high { color: #c55; }
    #drop-overlay { display: none; position: fixed; inset: 0; z-index: 100;
                    background: rgba(74,74,138,0.8); align-items: center;
                    justify-content: center; font-size: 2rem; color: #fff;
                    border: 4px dashed #7ec8e3; }
    #drop-overlay.active { display: flex; }
    /* #119 Session tab strip */
    #session-bar { display: flex; gap: 0; align-items: center; background: #0d0d22;
                   border-bottom: 1px solid #1a1a30; padding: 0.1rem 0.4rem; overflow-x: auto;
                   white-space: nowrap; flex-shrink: 0; }
    .session-tab { font-family: monospace; font-size: 0.78rem; padding: 0.2rem 0.6rem;
                   cursor: pointer; color: #888; border: 1px solid transparent;
                   border-radius: 2px 2px 0 0; background: transparent; transition: color 0.2s; }
    .session-tab:hover  { color: #ccc; }
    .session-tab.active { color: #7ec8e3; border-color: #1a1a30; background: #1a1a2e; }
    #new-session-btn { font-family: monospace; font-size: 0.82rem; padding: 0.1rem 0.5rem;
                       cursor: pointer; color: #555; background: transparent; border: none;
                       margin-left: 0.3rem; }
    #new-session-btn:hover { color: #aaa; }
    /* D105: Claude bridge pane */
    #content-area { flex: 1; display: flex; overflow: hidden; min-height: 0; }
    #bridge-pane { width: 42%; border-left: 1px solid #2a1a40; display: flex;
                   flex-direction: column; min-height: 0; background: #130d1e; }
    #bridge-header { padding: 0.25rem 0.6rem; background: #0a0717;
                     border-bottom: 1px solid #2a1a40; font-size: 0.75rem;
                     color: #c8a0ff; flex-shrink: 0; }
    #bridge-chat { flex: 1; overflow-y: auto; padding: 0.6rem;
                   display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.88rem; }
    .msg-claude .author { color: #c8a0ff; font-weight: bold; }
    #back-row { display: flex; gap: 0.4rem; padding: 0.3rem 0.4rem;
                border-top: 1px solid #2a1a40; flex-shrink: 0; }
    #back-input { flex: 1; background: #180d26; color: #e0e0e0; border: 1px solid #4a2a6a;
                  padding: 0.3rem 0.5rem; font-family: monospace; font-size: 0.85rem;
                  resize: none; min-height: 2em; }
    #cc-toggle { font-family: monospace; font-size: 0.78rem; color: #555;
                 background: transparent; border: 1px solid #444;
                 padding: 0.2rem 0.5rem; cursor: pointer; }
    #cc-toggle.active { color: #c8a0ff; border-color: #c8a0ff; }
  </style>
</head>
<body>
  <div id="drop-overlay">Drop file to send to Igor</div>
  <div id="session-bar">
    <span class="session-tab active" data-sid="shared" onclick="switchSession('shared')">shared</span>
    <button id="new-session-btn" onclick="newSession()" title="New session">+</button>
  </div>
  <div id="content-area">
    <div id="chat"></div>
    <div id="bridge-pane" style="display:none">
      <div id="bridge-header">◈ Claude bridge  <span id="bridge-count" style="color:#555;float:right"></span></div>
      <div id="bridge-chat"></div>
      <div id="back-row">
        <textarea id="back-input" placeholder="→ Claude only (back channel)…" rows="2" autocomplete="off"></textarea>
        <button onclick="sendBack()">→CC</button>
      </div>
    </div>
  </div>
  <div id="status-bar">●  idle</div>
  <div id="name-row">
    <span id="conn-led" title="Connection status">●</span>
    <label for="sender-name">Your name:</label>
    <input id="sender-name" type="text" value="akien" maxlength="32" autocomplete="off">
    <button id="cc-toggle" onclick="toggleCC()" title="Toggle Claude bridge pane">CC</button>
    <button onclick="changeFontSize(-1)" title="Decrease font size" style="padding:0.2rem 0.5rem;font-size:0.85rem;">A-</button>
    <button onclick="changeFontSize(1)" title="Increase font size" style="padding:0.2rem 0.5rem;font-size:0.85rem;">A+</button>
  </div>
  <div id="input-row">
    <textarea id="input" placeholder="Message Igor..." autocomplete="off" rows="4"></textarea>
    <button onclick="sendMsg()">Send</button>
    <button onclick="document.getElementById('file-input').click()">📎</button>
    <input id="file-input" type="file" style="display:none" onchange="uploadFile(this)">
  </div>
  <div id="dashboard"><span>Connecting...</span><span id="ring-toggle" onclick="toggleRing()" title="Toggle ring feed">▼ ring</span><span id="surprise-toggle" onclick="toggleSurprise()" title="Toggle prediction surprise feed">▼ surprise</span></div>
  <div id="ring-feed"><table id="ring-table"><tr><td colspan="2">loading…</td></tr></table></div>
  <div id="surprise-feed"><table id="surprise-table"><tr><td>loading…</td></tr></table></div>
  <script>
    const chat       = document.getElementById('chat');
    const input      = document.getElementById('input');
    const senderName = document.getElementById('sender-name');
    const dash       = document.getElementById('dashboard');
    const status     = document.getElementById('status-bar');
    const overlay    = document.getElementById('drop-overlay');
    const ringFeed      = document.getElementById('ring-feed');
    const ringTable     = document.getElementById('ring-table');
    const surpriseFeed  = document.getElementById('surprise-feed');
    const surpriseTable = document.getElementById('surprise-table');
    let ws, dragDepth = 0, ringOpen = false, surpriseOpen = false;
    const _urlSession = new URLSearchParams(location.search).get('session') || 'shared';
    let currentSession = _urlSession;
    const sessionMsgs = {'shared': []};   // session_id → [{author,content,ts}, ...]
    if (_urlSession !== 'shared') sessionMsgs[_urlSession] = [];
    const sessionBar = document.getElementById('session-bar');

    // Persist sender name in localStorage + cookie (cookie survives harder refreshes)
    function _saveName(n) {
      localStorage.setItem('igor_sender_name', n);
      document.cookie = 'igor_user=' + encodeURIComponent(n) + '; path=/; max-age=31536000; SameSite=Lax';
    }
    function _loadName() {
      // Cookie takes priority (survives localStorage clear); fall back to localStorage
      const _ck = document.cookie.split(';').map(c => c.trim())
        .find(c => c.startsWith('igor_user='));
      if (_ck) return decodeURIComponent(_ck.split('=')[1]);
      return localStorage.getItem('igor_sender_name') || '';
    }
    const _savedName = _loadName();
    if (_savedName) senderName.value = _savedName;
    senderName.addEventListener('change', () => _saveName(senderName.value));

    // ── #271 Font size controls ───────────────────────────────────────────────
    let _fontSize = parseFloat(localStorage.getItem('igor_font_size') || '0.95');
    function _applyFontSize() {
      document.getElementById('chat').style.fontSize = _fontSize + 'rem';
    }
    function changeFontSize(delta) {
      _fontSize = Math.min(Math.max(_fontSize + delta * 0.1, 0.6), 2.0);
      _fontSize = Math.round(_fontSize * 100) / 100;
      localStorage.setItem('igor_font_size', String(_fontSize));
      _applyFontSize();
    }
    _applyFontSize();

    // ── #119 Session management ──────────────────────────────────────────────
    function _renderSessionBar() {
      // Rebuild tab strip from sessionMsgs keys
      // Keep existing tabs, add new ones, preserve order
      const existing = new Set([...sessionBar.querySelectorAll('.session-tab')].map(t => t.dataset.sid));
      Object.keys(sessionMsgs).forEach(sid => {
        if (!existing.has(sid)) {
          const tab = document.createElement('span');
          tab.className = 'session-tab';
          tab.dataset.sid = sid;
          tab.textContent = sid;
          tab.onclick = () => switchSession(sid);
          sessionBar.insertBefore(tab, document.getElementById('new-session-btn'));
        }
      });
      sessionBar.querySelectorAll('.session-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.sid === currentSession);
      });
    }

    function _renderSession(sid) {
      chat.innerHTML = '';
      (sessionMsgs[sid] || []).forEach(m => {
        const cls = m.author === 'igor' ? 'igor' : m.author === 'claude-code' ? 'cc' : 'user';
        const label = m.author === 'igor' ? 'Igor' : m.author === 'claude-code' ? 'CC>' : (m.author || 'You');
        addMsg(cls, label, m.content);
      });
    }

    function switchSession(sid) {
      if (!sessionMsgs[sid]) sessionMsgs[sid] = [];
      currentSession = sid;
      history.replaceState({}, '', sid === 'shared' ? '/' : '/?session=' + encodeURIComponent(sid));
      _renderSessionBar();
      _renderSession(sid);
      if (ws && ws.readyState === 1)
        ws.send(JSON.stringify({type: 'join_session', session_id: sid}));
    }

    function newSession() {
      const name = prompt('Session name (blank for random):');
      if (name === null) return;
      const sid = name.trim() || 'session-' + Date.now().toString(36);
      switchSession(sid);
    }

    function toggleRing() {
      ringOpen = !ringOpen;
      ringFeed.className = ringOpen ? 'open' : '';
      document.getElementById('ring-toggle').textContent = (ringOpen ? '▲' : '▼') + ' ring';
    }

    function toggleSurprise() {
      surpriseOpen = !surpriseOpen;
      surpriseFeed.className = surpriseOpen ? 'open' : '';
      document.getElementById('surprise-toggle').textContent = (surpriseOpen ? '▲' : '▼') + ' surprise';
    }

    function updateSurprise(entries, avg) {
      if (!entries || !entries.length) {
        surpriseTable.innerHTML = '<tr><td>no surprise entries yet</td></tr>'; return;
      }
      surpriseTable.innerHTML = entries.map(e => {
        const t = new Date(e.ts * 1000).toLocaleTimeString();
        return '<tr><td>' + t + ' ' + esc(e.content) + '</td></tr>';
      }).join('');
      // Update avg badge in status bar
      const el = document.getElementById('surprise-avg');
      if (el && avg !== null && avg !== undefined) {
        el.textContent = 'Δ' + Number(avg).toFixed(2);
        el.className = avg < 0.2 ? 'low' : avg < 0.5 ? 'mid' : 'high';
      }
    }

    function updateRing(entries) {
      if (!entries || !entries.length) { ringTable.innerHTML = '<tr><td colspan="2">no ring entries</td></tr>'; return; }
      ringTable.innerHTML = entries.map(r => {
        const t = new Date(r.ts * 1000).toLocaleTimeString();
        return '<tr><td class="cat">[' + esc(r.category) + '] ' + t + '</td><td>' + esc(r.content) + '</td></tr>';
      }).join('');
    }

    function updateStatus(m) {
      const busy = m.busy === true;
      status.className = busy ? 'busy' : '';
      const tier  = m.tier  ? ' [' + m.tier + ']' : '';
      const input = m.input ? ' — "' + m.input + '"' : '';
      status.textContent = (busy ? '⚙ ' : '● ') + (m.action || (busy ? 'processing' : 'idle')) + tier + input;
    }

    function esc(s) {
      return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function parseMarkdown(raw) {
      // Line-by-line parser — handles mixed block/inline without regex paragraph confusion
      function fmt(s) {
        // Inline formatting on already-escaped text
        s = s.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
        s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
        return s;
      }
      const lines = raw.split('\n');
      const out = [];
      let inCode = false, codeLang = '', codeLines = [];
      let inUl = false, inOl = false;
      let paraLines = [];

      function flushPara() {
        if (!paraLines.length) return;
        out.push('<p>' + paraLines.join('<br>') + '</p>');
        paraLines = [];
      }
      function flushList() {
        if (inUl) { out.push('</ul>'); inUl = false; }
        if (inOl) { out.push('</ol>'); inOl = false; }
      }

      for (const line of lines) {
        // ── Code block ──────────────────────────────────────────────────────
        if (line.startsWith('```')) {
          if (inCode) {
            out.push('<pre><code>' + esc(codeLines.join('\n')) + '</code></pre>');
            codeLines = []; inCode = false;
          } else {
            flushPara(); flushList();
            codeLang = line.slice(3).trim();
            inCode = true;
          }
          continue;
        }
        if (inCode) { codeLines.push(line); continue; }

        // ── Blank line ──────────────────────────────────────────────────────
        if (!line.trim()) { flushPara(); flushList(); continue; }

        // ── Heading ─────────────────────────────────────────────────────────
        const hm = line.match(/^(#{1,3}) (.+)$/);
        if (hm) {
          flushPara(); flushList();
          const lv = hm[1].length;
          out.push('<h' + lv + '>' + fmt(esc(hm[2])) + '</h' + lv + '>');
          continue;
        }

        // ── HR ───────────────────────────────────────────────────────────────
        if (/^---+$/.test(line)) { flushPara(); flushList(); out.push('<hr>'); continue; }

        // ── Blockquote ───────────────────────────────────────────────────────
        const bq = line.match(/^> (.+)$/);
        if (bq) { flushPara(); flushList(); out.push('<blockquote>' + fmt(esc(bq[1])) + '</blockquote>'); continue; }

        // ── Unordered list item ──────────────────────────────────────────────
        const ul = line.match(/^[ \t]*[-*] (.+)$/);
        if (ul) {
          flushPara();
          if (!inUl) { flushList(); out.push('<ul>'); inUl = true; }
          out.push('<li>' + fmt(esc(ul[1])) + '</li>');
          continue;
        }

        // ── Ordered list item ────────────────────────────────────────────────
        const ol = line.match(/^\d+\. (.+)$/);
        if (ol) {
          flushPara();
          if (!inOl) { flushList(); out.push('<ol>'); inOl = true; }
          out.push('<li>' + fmt(esc(ol[1])) + '</li>');
          continue;
        }

        // ── Plain text — accumulate into paragraph ───────────────────────────
        flushList();
        paraLines.push(fmt(esc(line)));
      }

      flushPara(); flushList();
      if (inCode) out.push('<pre><code>' + esc(codeLines.join('\n')) + '</code></pre>');
      return out.join('\n');
    }

    function addMsg(cls, author, content) {
      const d = document.createElement('div');
      d.className = 'msg msg-' + cls;
      if (author) {
        const s = document.createElement('span');
        s.className = 'author';
        s.textContent = author + ':';
        d.appendChild(s);
      }
      // Igor messages use <div> (block elements inside <span> is invalid HTML)
      const c = document.createElement(cls === 'igor' ? 'div' : 'span');
      if (cls === 'igor') {
        c.className = 'content md';
        c.innerHTML = parseMarkdown(content);
      } else {
        c.className = 'content';
        c.textContent = content;
      }
      d.appendChild(c);
      chat.appendChild(d);
      chat.scrollTop = chat.scrollHeight;
    }

    const led = document.getElementById('conn-led');
    let _connectedOnce = false;
    let _disconnectedMsgShown = false;
    let _retryDelay = 2000;  // #196: exponential backoff, resets on successful connect

    function setLed(on) {
      led.classList.toggle('on', on);
      led.classList.toggle('off', !on);
      led.title = on ? 'Connected' : 'Disconnected — retrying…';
    }

    function connect() {
      ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
      ws.onopen  = () => {
        setLed(true);
        _retryDelay = 2000;  // reset backoff on success
        if (!_connectedOnce) { addMsg('system', '', 'Connected to Igor.'); _connectedOnce = true; }
        else                  { addMsg('system', '', 'Reconnected.'); }
        _disconnectedMsgShown = false;
        // Re-identify from cookie so Igor knows who we are without asking again
        const _cookieName = _loadName();
        if (_cookieName) ws.send(JSON.stringify({type: 'identify', name: _cookieName}));
        // Join/re-join current session so server routes to the right session
        ws.send(JSON.stringify({type: 'join_session', session_id: currentSession}));
      };
      ws.onerror = () => { ws.close(); };  // #196: ensure onclose fires on error too
      ws.onclose = () => {
        setLed(false);
        if (!_disconnectedMsgShown) {
          addMsg('system', '', 'Disconnected. Retrying…');
          _disconnectedMsgShown = true;
        }
        setTimeout(connect, _retryDelay);
        _retryDelay = Math.min(_retryDelay * 2, 30000);  // 2s→4s→8s→…→30s cap
      };
      ws.onmessage = e => {
        const m = JSON.parse(e.data);
        if (m.type === 'message') {
          const sid = m.session_id || 'shared';
          if (!sessionMsgs[sid]) sessionMsgs[sid] = [];
          sessionMsgs[sid].push(m);
          if (sessionMsgs[sid].length > 50) sessionMsgs[sid].shift();
          _renderSessionBar();
          // Only render if this is the active session
          if (sid === currentSession) {
            const cls = m.author === 'igor' ? 'igor' : m.author === 'claude-code' ? 'cc' : 'user';
            const label = m.author === 'igor' ? 'Igor' : m.author === 'claude-code' ? 'CC>' : (m.author || 'You');
            addMsg(cls, label, m.content);
          }
        } else if (m.type === 'session_history') {
          const sid = m.session_id || 'shared';
          sessionMsgs[sid] = m.messages || [];
          _renderSessionBar();
          if (sid === currentSession) _renderSession(sid);
        } else if (m.type === 'file_dropped')
          addMsg('system', '', '📎 ' + m.filename + ' received in inbox');
        else if (m.type === 'activity')
          updateStatus(m);
        else if (m.type === 'name_resolved') {
          senderName.value = m.name;
          _saveName(m.name);
          addMsg('system', '', 'Igor has learned your name: ' + m.name);
        }
      };
    }

    function sendMsg() {
      const rawText = input.value.trim();
      if (!rawText || !ws || ws.readyState !== 1) return;
      const name = (senderName.value.trim() || 'akien').toLowerCase();
      ws.send(JSON.stringify({type: 'message', content: rawText, author: name, session_id: currentSession}));
      input.value = '';
      // D105: if CC bridge pane is open, also send as shared channel
      if (ccEnabled) sendToBridge(rawText, 'shared');
    }
    // Enter sends; Shift+Enter inserts newline in textarea
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(); }
    });

    async function uploadFile(el) {
      const file = el.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/upload', {method: 'POST', body: fd});
      const j = await r.json();
      addMsg('system', '', '📎 ' + j.filename + ' uploaded to inbox');
      el.value = '';
    }

    /* Drag-and-drop file onto chat */
    document.addEventListener('dragenter', e => {
      if (e.dataTransfer.types.includes('Files')) { dragDepth++; overlay.classList.add('active'); }
    });
    document.addEventListener('dragleave', () => {
      if (--dragDepth <= 0) { dragDepth = 0; overlay.classList.remove('active'); }
    });
    document.addEventListener('dragover', e => e.preventDefault());
    document.addEventListener('drop', async e => {
      e.preventDefault(); dragDepth = 0; overlay.classList.remove('active');
      const file = e.dataTransfer.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/upload', {method: 'POST', body: fd});
      const j = await r.json();
      addMsg('system', '', '📎 ' + j.filename + ' dropped into inbox');
    });

    async function pollDash() {
      try {
        const r = await fetch('/api/dashboard');
        const d = await r.json();
        const parts = [];
        if (d.memory_count    !== undefined) parts.push('mem:' + d.memory_count);
        if (d.session_cost    !== undefined) parts.push('cost:$' + Number(d.session_cost).toFixed(4));
        if (d.last_valence    !== undefined) parts.push('val:' + (d.last_valence >= 0 ? '+' : '') + Number(d.last_valence).toFixed(2));
        if (d.last_friction   !== undefined) parts.push('f:' + Number(d.last_friction).toFixed(2));
        if (d.arbiter_pending !== undefined && d.arbiter_pending > 0)
          parts.push('⚠ arbiter:' + d.arbiter_pending);
        if (d.surprise_avg !== null && d.surprise_avg !== undefined)
          parts.push('<span id="surprise-avg">Δ' + Number(d.surprise_avg).toFixed(2) + '</span>');
        const toggle   = document.getElementById('ring-toggle');
        const stoggle  = document.getElementById('surprise-toggle');
        dash.innerHTML = (parts.length ? parts.map(p => '<span>' + p + '</span>').join('') : '<span>Igor online</span>');
        dash.appendChild(toggle);
        dash.appendChild(stoggle);
        if (d.ring_recent) updateRing(d.ring_recent);
        if (d.surprise_recent) updateSurprise(d.surprise_recent, d.surprise_avg);
      } catch(e) {}
    }

    // ── D105: Claude bridge pane ─────────────────────────────────────────────
    let ccEnabled = false;

    function toggleCC() {
      ccEnabled = !ccEnabled;
      document.getElementById('bridge-pane').style.display = ccEnabled ? 'flex' : 'none';
      document.getElementById('cc-toggle').classList.toggle('active', ccEnabled);
    }

    function addBridgeMsg(cls, author, content) {
      const bc = document.getElementById('bridge-chat');
      const d = document.createElement('div');
      d.className = 'msg msg-' + cls;
      if (author) {
        const s = document.createElement('span');
        s.className = 'author'; s.textContent = author + ':'; d.appendChild(s);
      }
      const c = document.createElement(cls === 'claude' ? 'div' : 'span');
      if (cls === 'claude') { c.className = 'content md'; c.innerHTML = parseMarkdown(content); }
      else { c.className = 'content'; c.textContent = content; }
      d.appendChild(c);
      bc.appendChild(d);
      bc.scrollTop = bc.scrollHeight;
    }

    async function sendToBridge(message, channel) {
      if (channel === 'shared') addBridgeMsg('user', 'you', message);
      const thinkId = 'think-' + Date.now();
      const bc = document.getElementById('bridge-chat');
      const thinkEl = document.createElement('div');
      thinkEl.id = thinkId; thinkEl.className = 'msg msg-system';
      thinkEl.textContent = '◈ Claude is thinking…'; bc.appendChild(thinkEl);
      bc.scrollTop = bc.scrollHeight;
      try {
        const r = await fetch('/api/bridge_chat', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message, channel})
        });
        const j = await r.json();
        const el = document.getElementById(thinkId);
        if (el) el.remove();
        if (j.reply) {
          addBridgeMsg('claude', 'Claude', j.reply);
          const cnt = document.getElementById('bridge-count');
          if (cnt) cnt.textContent = j.message_count + ' msgs';
        } else {
          addBridgeMsg('system', '', 'Bridge error: ' + (j.error || 'unknown'));
        }
      } catch(e) {
        const el = document.getElementById(thinkId);
        if (el) el.remove();
        addBridgeMsg('system', '', 'Bridge unavailable: ' + e.message);
      }
    }

    async function sendBack() {
      const bi = document.getElementById('back-input');
      const msg = bi.value.trim(); if (!msg) return; bi.value = '';
      await sendToBridge(msg, 'back');
    }

    document.getElementById('back-input').addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendBack(); }
    });

    // Show tab for URL-specified session before WebSocket connects
    if (_urlSession !== 'shared') _renderSessionBar();
    connect();
    pollDash();
    setInterval(pollDash, 5000);
  </script>
</body>
</html>"""

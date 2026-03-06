"""
Web server for Igor — Starlette/uvicorn, runs in a background daemon thread.

Endpoints:
  GET  /                    → serve web_ui/dist/index.html (fallback HTML if not built)
  GET  /assets/{path}       → serve web_ui/dist/assets/
  WS   /ws                  → WebSocket chat (bidirectional)
  POST /api/upload          → save file to inbox, notify Igor
  GET  /api/outbox          → JSON list of outbox files with size/mtime
  GET  /api/outbox/{file}   → download file from outbox
  GET  /api/dashboard       → JSON stats snapshot

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
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

# ── Paths ──────────────────────────────────────────────────────────────────────
_INSTANCE_DIR = Path.home() / ".TheIgors" / "igor_wild_0001"
INBOX_DIR  = _INSTANCE_DIR / "inbox"
OUTBOX_DIR = _INSTANCE_DIR / "outbox"
_DIST_DIR  = Path(__file__).parent.parent.parent.parent / "web_ui" / "dist"

# ── Thread-safe queue: web messages → Igor ────────────────────────────────────
incoming: queue.Queue = queue.Queue()

# ── Per-client asyncio queues for broadcast ───────────────────────────────────
_client_queues: list = []
_client_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None

# ── Shared state ──────────────────────────────────────────────────────────────
_stats_fn = None  # callable → dict; set by start(); Igor class owns all state (change.30)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_dirs():
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)


# ── Public API ────────────────────────────────────────────────────────────────

def send(text: str):
    """Broadcast an Igor response to all connected WebSocket clients. Thread-safe."""
    _broadcast(json.dumps({
        "type": "message",
        "author": "igor",
        "content": text,
        "ts": _ts(),
    }))


def broadcast_activity(state: dict):
    """Broadcast a live activity state update to all connected WebSocket clients.

    state dict keys (all optional):
        action  — human-readable description of what Igor is doing right now
        tier    — current reasoning tier ("tier.2", "tier.3", etc.) or ""
        input   — first 60 chars of the current user input
        busy    — bool: True while processing, False when idle
    """
    _broadcast(json.dumps({
        "type": "activity",
        "ts": _ts(),
        **state,
    }))



def _broadcast(payload: str):
    """Fan out a JSON payload to every connected WebSocket client."""
    if _loop is None:
        return
    with _client_lock:
        queues = list(_client_queues)
    for q in queues:
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
    incoming.put({"content": f"[File uploaded: {safe_name}]", "filename": safe_name, "author": "web-user"})
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
    except OSError:
        pass
    return JSONResponse(files)


async def _api_outbox_download(request: Request):
    safe = Path(request.path_params["filename"]).name  # no path traversal
    path = OUTBOX_DIR / safe
    if not path.exists():
        return Response("Not found", status_code=404)
    return FileResponse(str(path), filename=safe)


async def _api_dashboard(request: Request):
    data: dict = {}
    if _stats_fn is not None:
        try:
            data = dict(_stats_fn())
        except Exception:
            pass
    data["ts"] = _ts()
    return JSONResponse(data)


async def _ws_endpoint(ws: WebSocket):
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    with _client_lock:
        _client_queues.append(q)

    async def _receive():
        try:
            while True:
                data = await ws.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "message":
                    content = msg.get("content", "").strip()
                    # Allow client to self-identify (e.g. "claude-code"); default "web-user"
                    author = msg.get("author", "web-user")
                    if content:
                        incoming.put({"content": content, "author": author, "client_id": id(ws)})
                        # Echo user message to all clients so multi-tab works
                        _broadcast(json.dumps({
                            "type": "message",
                            "author": author,
                            "content": content,
                            "ts": _ts(),
                        }))
        except Exception:
            pass

    async def _forward():
        try:
            while True:
                payload = await q.get()
                await ws.send_text(payload)
        except Exception:
            pass

    recv = asyncio.ensure_future(_receive())
    fwd  = asyncio.ensure_future(_forward())
    await asyncio.wait([recv, fwd], return_when=asyncio.FIRST_COMPLETED)
    for t in (recv, fwd):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    with _client_lock:
        try:
            _client_queues.remove(q)
        except ValueError:
            pass


# ── Starlette app factory ─────────────────────────────────────────────────────

def _make_app() -> Starlette:
    async def on_startup():
        global _loop
        _loop = asyncio.get_running_loop()

    routes = [
        Route("/", _index),
        WebSocketRoute("/ws", _ws_endpoint),
        Route("/api/upload", _api_upload, methods=["POST"]),
        Route("/api/outbox", _api_outbox_list),
        Route("/api/outbox/{filename}", _api_outbox_download),
        Route("/api/dashboard", _api_dashboard),
    ]

    # Serve compiled Svelte assets if the UI has been built
    assets_dir = _DIST_DIR / "assets"
    if assets_dir.exists():
        routes.append(Mount("/assets", app=StaticFiles(directory=str(assets_dir)), name="assets"))

    return Starlette(routes=routes, on_startup=[on_startup])


# ── Server lifecycle ──────────────────────────────────────────────────────────

_server_thread: Optional[threading.Thread] = None


def start(stats_fn=None):
    """Start the web server in a background daemon thread. Non-blocking.

    stats_fn: callable () → dict — Igor.get_stats(); called by /api/dashboard.
    Igor owns all state; web server owns none (change.30 gateway pattern).
    """
    global _server_thread, _stats_fn
    _stats_fn = stats_fn
    _ensure_dirs()

    if _server_thread and _server_thread.is_alive():
        return

    port = int(os.getenv("IGOR_WEB_PORT", "8080"))

    def _run():
        app = _make_app()
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
        server = uvicorn.Server(config)
        asyncio.run(server.serve())

    _server_thread = threading.Thread(target=_run, daemon=True, name="web-server")
    _server_thread.start()


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
    #drop-overlay { display: none; position: fixed; inset: 0; z-index: 100;
                    background: rgba(74,74,138,0.8); align-items: center;
                    justify-content: center; font-size: 2rem; color: #fff;
                    border: 4px dashed #7ec8e3; }
    #drop-overlay.active { display: flex; }
  </style>
</head>
<body>
  <div id="drop-overlay">Drop file to send to Igor</div>
  <div id="chat"></div>
  <div id="status-bar">●  idle</div>
  <div id="name-row">
    <label for="sender-name">Your name:</label>
    <input id="sender-name" type="text" value="akien" maxlength="32" autocomplete="off">
  </div>
  <div id="input-row">
    <textarea id="input" placeholder="Message Igor..." autocomplete="off" rows="1"></textarea>
    <button onclick="sendMsg()">Send</button>
    <button onclick="document.getElementById('file-input').click()">📎</button>
    <input id="file-input" type="file" style="display:none" onchange="uploadFile(this)">
  </div>
  <div id="dashboard"><span>Connecting...</span><span id="ring-toggle" onclick="toggleRing()" title="Toggle ring feed">▼ ring</span></div>
  <div id="ring-feed"><table id="ring-table"><tr><td colspan="2">loading…</td></tr></table></div>
  <script>
    const chat       = document.getElementById('chat');
    const input      = document.getElementById('input');
    const senderName = document.getElementById('sender-name');
    const dash       = document.getElementById('dashboard');
    const status     = document.getElementById('status-bar');
    const overlay    = document.getElementById('drop-overlay');
    const ringFeed   = document.getElementById('ring-feed');
    const ringTable  = document.getElementById('ring-table');
    let ws, dragDepth = 0, ringOpen = false;

    // Persist sender name in localStorage
    const _savedName = localStorage.getItem('igor_sender_name');
    if (_savedName) senderName.value = _savedName;
    senderName.addEventListener('change', () => localStorage.setItem('igor_sender_name', senderName.value));

    function toggleRing() {
      ringOpen = !ringOpen;
      ringFeed.className = ringOpen ? 'open' : '';
      document.getElementById('ring-toggle').textContent = (ringOpen ? '▲' : '▼') + ' ring';
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

    function connect() {
      ws = new WebSocket('ws://' + location.host + '/ws');
      ws.onopen  = () => addMsg('system', '', 'Connected to Igor.');
      ws.onclose = () => { addMsg('system', '', 'Disconnected. Retrying…'); setTimeout(connect, 2000); };
      ws.onmessage = e => {
        const m = JSON.parse(e.data);
        if (m.type === 'message')
          addMsg(m.author === 'igor' ? 'igor' : 'user', m.author === 'igor' ? 'Igor' : 'You', m.content);
        else if (m.type === 'file_dropped')
          addMsg('system', '', '📎 ' + m.filename + ' received in inbox');
        else if (m.type === 'activity')
          updateStatus(m);
      };
    }

    function sendMsg() {
      const rawText = input.value.trim();
      if (!rawText || !ws || ws.readyState !== 1) return;
      const name = (senderName.value.trim() || 'akien').toLowerCase();
      const text = name === 'akien' ? rawText : name + ': ' + rawText;
      ws.send(JSON.stringify({type: 'message', content: text}));
      input.value = '';
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
        const toggle = document.getElementById('ring-toggle');
        dash.innerHTML = (parts.length ? parts.map(p => '<span>' + p + '</span>').join('') : '<span>Igor online</span>');
        dash.appendChild(toggle);
        if (d.ring_recent) updateRing(d.ring_recent);
      } catch(e) {}
    }

    connect();
    pollDash();
    setInterval(pollDash, 5000);
  </script>
</body>
</html>"""

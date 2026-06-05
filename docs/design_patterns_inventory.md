# Design Patterns Inventory — UnseenUniversity / Igor

Machine-readable context block. Loaded by the pre-inference assembler before
any sprint involving device, shim, bus, or skeleton code.

Generated: 2026-06-05  
Source files scanned: `devices/`, `unseen_university/`, `skeleton/`

---

## PATTERN-001: BaseDevice / BaseShim Lifecycle

**Kind:** Structural / Lifecycle  
**When to use:** Every rack-registered component.

**Shape:**
- `BaseDevice` (in `unseen_university/device.py`) defines the rack contract:
  `who_am_i()`, `requirements()`, `capabilities()`, `comms()`, `health()`,
  `uptime()`, `startup_errors()`, `logs()`, `update_info()`, `where_and_how()`,
  `restart()`, `block()`, `halt()`, `recovery()`.
- `BaseShim` (in `unseen_university/shim.py`) defines lifecycle management:
  `start() → bool`, `stop() → bool`, `restart() → bool`,
  `self_test() → {passed, details}`, `rollback() → None`.
- Each device lives in `devices/<name>/device.py` and inherits from `BaseDevice`.
  Its companion shim lives in `devices/<name>/shim.py` and inherits from `BaseShim`.

**Canonical examples:**
- `devices/auditor/shim.py` — no-op shim (in-process device, no external process)
- `devices/granny/shim.py` — shim managing an external daemon via PID file + SIGTERM
- `devices/archivist/shim.py` — shim holding a `_device` reference, start() instantiates it

**Invariant:** `rollback()` must be idempotent — safe to call even if `start()` did nothing.

---

## PATTERN-002: IDLE Loop (Push Receive — No Polling)

**Kind:** Concurrency / Message delivery  
**When to use:** Any component that receives envelopes from a comms:// mailbox.

**Shape:**
```python
def run_forever(self, stop: threading.Event | None = None) -> None:
    while stop is None or not stop.is_set():
        try:
            woke = self._imap.idle_wait(MAILBOX, timeout_s=_KEEPALIVE_S)
            if woke:
                self.pump()  # fetch_unseen() + dispatch
        except Exception as exc:
            log.warning("...: IDLE loop error: %s", exc)
```

- `idle_wait()` blocks until server pushes EXISTS or timeout fires.
- On EXISTS → call `pump()` which calls `fetch_unseen()` then dispatches each envelope.
- On timeout (25 min keepalive) → re-enter IDLE without fetching (RFC 2177 compliance).
- `pump()` is the testable seam — tests call it directly without needing IDLE.
- The loop lives in the component (Listener/Aggregator), **not** in `BaseShim`.
  The shim starts the component's background thread in `start()`.

**Canonical examples:**
- `unseen_university/announce/listener.py` — `AnnounceListener.run_forever()` (~15 lines)
- `unseen_university/announce/idle_loop.py` — `AnnounceIdleLoop` (lower-level IDLE primitives)
- `unseen_university/devices/librarian/health_aggregator.py` — health rollup over IDLE

---

## PATTERN-003: Request / Response Envelope

**Kind:** Communication  
**When to use:** Agent-to-agent RPC over the bus.

**Shape:**
- Every bus `Envelope` carries `from_device` (sender) and `to_device` (destination).
- Request: sender appends to target's mailbox.
- Response: target appends reply to `env.from_device` mailbox.
- When reply should go to a different address, include `reply_to` in the payload dict.
- No RPC library needed — the IDLE loop delivers the reply.

**Wire shape (Envelope fields):**
```python
Envelope.now(
    from_device="my-device",
    to_device="announce",        # or "igor-wild-0001", "CC.0", etc.
    payload={"kind": "...", ...}
)
```

**comms:// addressing:**
- `comms://announce`, `comms://CC.0`, `comms://igor-wild-0001`, `comms://Shared`
- Surface suffixes: `comms://igor-wild-0001.console`, `comms://igor-wild-0001.mcp`
- Pub/sub falls out naturally: `comms://Shared` → all IDLE subscribers on Shared mailbox.

**Canonical example:**
- `unseen_university/announce/` — announce → manifest reply flow is the canonical live demo.

---

## PATTERN-004: Flat-File Registry (Atomic Write)

**Kind:** Boot-time state / Persistence  
**When to use:** Any state that must be available before Postgres is up.

**Shape:**
- State stored as JSON at a well-known path (default: `~/.unseen_university/devices.json`).
- All writes go through `_atomic_write()`:
  ```python
  def _atomic_write(self, data: dict) -> None:
      tmp = self._path.with_suffix(".tmp")
      tmp.write_text(json.dumps(data, indent=2))
      os.replace(tmp, self._path)     # atomic on POSIX
  ```
- Reads fall back to empty dict on corrupt / missing file (never crash on bad JSON).

**Canonical example:** `skeleton/registry.py` — `DeviceRegistry`

**Rule:** The skeleton must never depend on Postgres — skeleton manages Postgres,
so a Postgres dependency makes skeleton unable to restart Postgres (circular).

---

## PATTERN-005: Worker Availability Semaphore

**Kind:** Concurrency / Dispatch gating  
**When to use:** Any dispatcher that must not double-dispatch a worker.

**Shape:**
- Two files per worker in `~/.granny/available/`:
  - `{worker_id}.available.true` — opts in
  - `{worker_id}.available.false` — blocks (takes precedence)
- Neither file = unavailable.
- `is_available(worker_id)` checks `.false` first, then `.true`.
- `mark_unavailable(worker_id)` writes `.false`, removes `.true`.
- `mark_available(worker_id)` writes `.true`, removes `.false`.

**Canonical example:** `devices/granny/availability.py`

**Workers:** `CC.0`, `DickSimnel.0`

---

## PATTERN-006: Dispatch Handshake (Ack → Started → Timeout)

**Kind:** Distributed coordination  
**When to use:** Granny dispatching a ticket to a worker that acknowledges via envelope.

**Shape:**  
Three-phase envelope protocol managed by `_DispatchHandshake` (in `unseen_university/shim.py`):
1. `dispatch_ack` — sent immediately on `start()`, Granny marks ticket acked.
2. `dispatch_started` — sent when `deliver_fn()` returns True (worker accepted), Granny marks in_progress.
3. `dispatch_timeout` — sent after 600s with no pickup, Granny escalates.

All three carry `{kind, ticket_id, from_device}` for correlation.  
Between ack and started: the handshake prods `deliver_fn()` every 120s.

**Canonical example:** `unseen_university/shim.py` — `_DispatchHandshake`

---

## PATTERN-007: PID-File Daemon Management

**Kind:** Process lifecycle  
**When to use:** Any device that manages an external long-running process.

**Shape:**
- Daemon writes its PID to a well-known file on startup (e.g. `~/.granny/daemon.pid`).
- Shim's `self_test()` reads the PID file and sends signal 0 to check liveness:
  ```python
  pid = int(pid_file.read_text().strip())
  os.kill(pid, 0)   # raises ProcessLookupError if dead
  ```
- Shim's `stop()` reads PID file and sends SIGTERM.
- PID file absent or stale (ProcessLookupError) → daemon not running.

**Canonical example:** `devices/granny/shim.py`

**Self-heal hook:** When `self_test()` detects dead daemon + pending tickets,
it calls `start()` (or a subprocess restart) to revive. This is the
Granny-shim self-heal pattern (T-granny-shim-self-heal).

---

## PATTERN-008: Proxy / Intercept Layer

**Kind:** Structural / Cross-cutting concern  
**When to use:** Adding logging, caching, or learning to an existing dispatch path without changing call sites.

**Shape:**
- Proxy class wraps a `dispatch_fn` callable.
- `intercept(request, dispatch_fn)` runs pre-check → on miss → forward → post-action.
- Module-level `register_proxy()` / `clear_proxy()` let the shim wire the proxy into
  all existing instances without touching each call site.

**Canonical example:** `devices/archivist/proxy.py` — `ArchivistProxy.intercept()`

---

## PATTERN-009: Config Profile (YAML per Agent Type)

**Kind:** Configuration  
**When to use:** Any new agent type that needs allowed devices, permissions, or channel memberships.

**Shape:**
- YAML file in `config/profiles/<agent_type>.yaml`.
- Fields: `profile_version`, `agent_type`, `description`, `inherits`, `allowed_devices`,
  `device_permissions`, `channel_memberships`.
- The canonical source lives in the repo; a runtime copy syncs to
  `~/.unseen_university/profiles/` on install.

**Canonical examples:** `config/profiles/cc.yaml`, `config/profiles/igor.yaml`

---

## PATTERN-010: Shim-Holds-Device Reference

**Kind:** Structural  
**When to use:** In-process devices (no external subprocess) where the shim owns the object lifecycle.

**Shape:**
```python
class MyShim(BaseShim):
    def __init__(self):
        self._device: MyDevice | None = None

    def start(self) -> bool:
        self._device = MyDevice(...)
        return True

    def stop(self) -> bool:
        self._device = None
        return True

    def device(self) -> MyDevice | None:
        return self._device
```

- `start()` instantiates; `stop()` drops the reference.
- `self_test()` delegates to `self._device` for health check.
- **External state rule:** any field that matters for restart must live outside the shim
  (in a flat file or DB) — in-memory-only fields are lost on process restart.

**Canonical example:** `devices/archivist/shim.py` — `ArchivistShim`

---

## PATTERN-011: Announce / Identity Protocol

**Kind:** Service discovery  
**When to use:** Every agent that starts and wants to be reachable on the rack.

**Shape:**
1. Agent boots, constructs `IdentityEnvelope` with `agent_id`, `instance`, `box`, `pid`, etc.
2. Appends it to `comms://announce`.
3. `AnnounceListener` picks it up via IDLE loop, calls `AnnounceBroker.handle()`.
4. Broker resolves profile from `config/profiles/<agent_type>.yaml`.
5. Broker replies with `Manifest` to the announcing agent's mailbox.
6. Agent reads the Manifest to learn its allowed devices, channel memberships, permissions.

**Canonical files:**
- `unseen_university/announce/envelope.py` — `IdentityEnvelope` wire shape
- `unseen_university/announce/broker.py` — `AnnounceBroker`
- `unseen_university/announce/manifest.py` — `Manifest` assembly
- `unseen_university/announce/listener.py` — IDLE loop entry point

---

## Quick-Reference: Which pattern for which problem?

| Problem | Pattern |
|---|---|
| New rack device | PATTERN-001 (BaseDevice/BaseShim) |
| Receive messages without polling | PATTERN-002 (IDLE loop) |
| Agent-to-agent RPC | PATTERN-003 (Request/Response Envelope) |
| State that must load before Postgres | PATTERN-004 (Flat-file registry) |
| Prevent double-dispatch | PATTERN-005 (Availability semaphore) |
| Track worker pickup via envelopes | PATTERN-006 (Dispatch handshake) |
| External daemon with PID | PATTERN-007 (PID-file daemon) |
| Add cross-cutting behavior to dispatch | PATTERN-008 (Proxy/intercept) |
| Per-agent-type configuration | PATTERN-009 (Config profile) |
| In-process device with shim ownership | PATTERN-010 (Shim-holds-device) |
| Agent boot / service discovery | PATTERN-011 (Announce protocol) |

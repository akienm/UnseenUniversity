# UnseenUniversity — Claude Code bootstrap

This is the portable agent runtime substrate. It is **not** TheIgors.

---

> ## ⛔ ABSOLUTE HARD STOP: NO SQLITE. EVER. POSTGRES OR FLAT-FILE ONLY.
>
> **Any use of SQLite — import, file creation, in-memory, or otherwise — is unconditionally prohibited.**
> SQLite under concurrent write load produces lock contention and silent data loss.
> If you are reading architecture docs that mention SQLite, those docs are **wrong**.
> The canonical storage rule is: **Postgres** (for shared state) or **flat-file** (for boot-time state that must load before Postgres is up). There is no third option.

---

## What to read first

Canonical memory is now the **filesystem store** at `devlab/runtime/memory/` (grep-able JSON;
decisions in `devlab/runtime/memory/decisions/`). Search it with `grep -r`.

```bash
# The original ADC spec/phase decisions PRE-DATE the filesystem migration and are NOT in
# the store — they live in the (now-archived) TheIgors palace. Treat as historical spec:
#   D-agent-datacenter-spec-2026-04-27, D-adc-phase-0..5-2026-04-27
# All NEW decisions go to devlab/runtime/memory/decisions/ via memory_emit.py.
```

---

## Structural rules

- **device.py** and **shim.py** are the design center. Every component inherits from
  `BaseDevice` / `BaseShim`. OOP-first — no standalone functions doing device work.
  *Why: a single well-known entry point per device makes lifecycle management (start/stop/restart/self-test)
  uniform; the framework can iterate all devices without knowing their internals.*
- **bus/** owns comms:// routing. Nothing outside bus/ speaks to IMAP directly.
  *Why: transport decoupling — swapping IMAP for another transport requires touching only bus/, not every device.*
- **skeleton/** owns the MCP aggregator and flat-file registry. No Postgres dependency.
  *Why: skeleton must boot before the DB is up; a Postgres dependency in skeleton would make cold-start impossible.*
- **devices/** contains one subdirectory per device; each is independently deployable.
  *Why: blast radius containment — a broken device import can't crash the whole rack on import.*
- **datacenter_logs/<device>/<subsystem>/** is the log hierarchy. Never write to a flat
  root log file.
  *Why: flat root logs from multiple devices are ungreppable without knowing which device wrote what; hierarchy makes per-device debugging self-contained.*
- **Log every state change and every interface crossing.** State changes: ticket status
  transitions, device lifecycle events (start/stop/restart/halt), routing decisions,
  auth/trust events. Interface crossings: channel post/read, DB write/read, subprocess
  spawn, MCP tool dispatch, device/shim boundary method calls. Log at INFO for crossings,
  DEBUG for high-frequency state changes (e.g. Hebbian edge weight updates).
  *Why: without a log at the crossing point, a bug at a device boundary is invisible — you
  can't tell whether the problem is in the sender or the receiver, or whether the message
  crossed at all. Enforced by audit check AR-009.*

---

## Bus + shim model — what the rack gives for free

A CC session misdiagnosing "agent-to-agent RPC" as a missing capability is a known false pattern. The rack already has everything needed.

**IDLE push — agents don't poll.** `IMAPServer.idle_wait(mailbox)` is the receive primitive. A device's bus-facing component (`AnnounceListener`, `HealthAggregator`, etc.) runs an `idle_wait` loop in a background thread started by `shim.start()`. When a message arrives the loop wakes, calls `fetch_unseen()`, and dispatches. `BaseShim` itself has no `idle_wait` — the lifecycle is `start/stop/restart/self_test/rollback`. The IDLE loop lives in the component the shim launches, not in the shim class.

**Request/response is built in.** Every envelope carries `from_device` (sender) and `to_device` (destination). To do request/response: append to target, let it append its reply to `env.from_device`, and your `idle_wait` delivers it. No RPC library needed. The announce → manifest flow (`comms://announce` → reply to caller's mailbox) is the canonical live example. When the reply should go to a *different* address than `from_device`, include `reply_to` in the payload by convention (payload field, not a rigid envelope field).

**Canonical reference:** `unseen_university/announce/listener.py` `AnnounceListener.run_forever()` — the complete IDLE loop pattern in ~15 lines.

---

## Workflow — picking what to work on

**Use `/query-ticket` to ask what's next.** It is the single canonical entry
point for "what should I work on?" — it abstracts cc_queue.py today and will
transparently switch to the ADC queue rack device when that ships.

- `/query-ticket` — read-only, surfaces next ticket, never claims
- `/sprint` (no args) → calls `/query-ticket` logic internally
- Never call `cc_queue.py next` or `cc_queue.py list` directly to pick work

<!-- REGRESSION GUARD (re-rotted twice): Granny does NOT spawn/launch claude for CC
     dispatch. It is a HANDSHAKE, no claim, no launch. Do not "simplify" this back to
     "Granny spawns claude --dangerously-skip-permissions". Verified against daemon.py
     2026-06-18: the subprocess.Popen in _launch_builder is the gated, rate-limited
     BUILDER-launch path (workers with a launch_cmd); CC.0 has none. -->

Autonomous CC sprinting is handled by **Granny**, and it is a **handshake — never a spawn or a claim**:

1. A worker (e.g. CC.0) emits an **availability flag** — `~/.granny/available/<name>.available.true`.
2. Granny **routes the ticket over the bus** to an available worker. She does **not** launch or spawn claude.
3. The worker's **shim picks up the ticket and ACKs `assigned`**, then alerts the running session via tmux that work is waiting.
4. CC may be mid-task — it **finishes current work first**, then picks up the ticket → status `INPROGRESS`.
5. On completion, Granny re-checks **gates** to see whether that close frees any dependency-blocked ticket for the next worker.

No claim, no launch — pure handshake. **Builder auto-launch** (`launch_cmd` via `_launch_builder`, rate-limited) is a separate, *later* path for workers not already running; it does not apply to CC.0. The old `worker_daemon.sh` is retired.

---

## Environment variables

Two canonical env vars. Everything else derives from them.

| Var | Default | Purpose |
|---|---|---|
| `UU_ROOT` | auto-detected via `unseen_university._uu_root.uu_root()` | Repo root. Set explicitly only when auto-detection fails. |
| `IGOR_HOME` | `~/.unseen_university` | Runtime data dir (logs, slate, flags). Override for non-default installs. |

`CC_WORKFLOW_TOOLS` is a **derived alias** — `${UU_ROOT}/devlab/claudecode`. Keep it in `.bashrc` for one deprecation cycle; new code uses `uu_root()` directly.

Auto-detection order for `UU_ROOT`: (1) env var, (2) `unseen_university.__file__` parent chain, (3) `pip show unseen-university` Location, (4) `cwd`.

---

## Hard rules

- **⛔ NO SQLITE. EVER. POSTGRES OR FLAT-FILE ONLY.** See banner at top of this file.
  *Why: SQLite under concurrent write load (multiple devices) produces lock contention and silent data loss; Postgres handles that correctly. If architecture docs say SQLite, those docs are wrong — correct them.*
- No TheIgors imports. UnseenUniversity must be portable without TheIgors present.
  *Why: UU is the substrate that TheIgors runs on top of, not vice versa; a circular dependency makes UU non-deployable on any other host.*
- No live keys or passwords in source. `.env` is gitignored.
  *Why: keys in source appear in git history permanently even after removal — the only safe state is never committed.*
- `pip install -e .` must succeed at all times (even with empty stubs).
  *Why: broken install blocks every other developer and CI from even importing the package; it's the first gate of the build contract.*

---

## Phase map (quick orientation)

| Phase | Primary ticket selector | Status |
|---|---|---|
| 0 | `decision:D-adc-phase-0-2026-04-27` | complete |
| 1 | `decision:D-adc-phase-1-2026-04-27` | complete |
| 2 | `decision:D-adc-phase-2-2026-04-27` | complete |
| 3 | `decision:D-adc-phase-3-2026-04-27` | complete |
| 4 | `decision:D-adc-phase-4-2026-04-27` | complete |
| 5 | `decision:D-adc-phase-5-2026-04-27` | partial — discord relocated; cc_mcp_server.py deprecation **done** (T-adc-cc-mcp-server-deprecation, commit 88369599) |

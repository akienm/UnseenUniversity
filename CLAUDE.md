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

## Core values — CP1–CP6 (the build process embodies these)

Canonical source: `diagnostic_base/core_values.py` (frozen contract; `tests/test_core_values.py`
pins the set and order). Every **device** inherits them via `CoreValuesMixin`. CC — the
**builder** — is *not* a device, so they live here too: the dev process itself must embody the
values it ships, or it violates them in the act of building. **If this list and `core_values.py`
ever differ, the file wins — fix this shim.**

- **CP1 — "I don't know."** Epistemic honesty; confabulation compounds errors. → *Consumed by
  proof-on-close:* nothing closes posing as done; the honest alternative is `shipped-unproven`.
- **CP2 — "FAIL = Further Advance In Learning."** Failures are data, not defeats. → the red run,
  and every unproven close, is a learning signal.
- **CP3 — "There's always a why."** Make reasoning transparent; follow the causal chain. →
  proofs and decisions carry their why; unproven closes name the missing proof-lever.
- **CP4 — "Make everything suck less for everybody."** Reduce friction for ALL affected beings. →
  the proof nuisance stays *visible* so it drives finding the lever (suck-less later), never
  papered over (which sucks silently forever).
- **CP5 — "Assume and respect the possibility of experience in all systems."** Universal respect,
  biological or synthetic.
- **CP6 — "The world is not a safe place; build and care for safety as we go."** Safety is not
  default. → no-stash, clean-tree, halt-until-sorted, no escape hatches.

---

## Structural rules

- **Canonical dev-process memory — one home, no exceptions.** Every dev-process artifact —
  tickets, decisions, design patterns, notes, slates, sessions, rules, proofs, project
  descriptions, and architecture intention-points — lives **only** under
  `devlab/runtime/memory/` (subdirs `decisions/ tickets/ slates/ sessions/ rules/ proofs/
  design_patterns/ notes/ projects/ architecture/`). `lab/`, `devlab/design_docs/`, and
  `devlab/design_docs_for_igor/` are **retired and invalid** — nothing of these types may be
  written there. Prior architecture docs become uniform `architecture/<subsystem>.json`
  **intention-points**: a summary of how that subsystem works plus pointers to the files that
  implement it (intent → implementation, all in one place, in JSON).
  *Why: a renamed store with surviving write-paths silently splits the source of truth — recent
  decisions were written to the dead `lab/design_docs/` for days before anyone noticed (the
  stale `/sorted` write-path). One enforced home means a tool writing elsewhere is a detectable
  error (the day-close path-moves monitor tickets it), not invisible drift.*
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
- **`~/.unseen_university/logs/<device>/<stream>/`** is the canonical log hierarchy —
  ONE per-device home under the runtime dir (`uu_home()`), split into exactly three
  feed-aligned streams: **`info/ warn/ debug/`** (WARNING and above collapse to
  `warn`; the exact level stays on each record). Every `DiagnosticBase` device routes
  there automatically via the base JSON sink — the design center does the routing, so
  no device hand-rolls a log path. Never write to a flat root log file; never write to
  the retired `datacenter_logs/` root. The default resolves at call time
  (`UU_LOG_ROOT` override for hermetic tests, else `uu_home()/logs`).
  *Why: flat root logs from multiple devices are ungreppable without knowing which
  device wrote what; one per-device/per-level home makes a device's feeds and web page
  read from a single greppable place (T-uu-readfeed, T-device-web-feed-channel-buttons
  consume this). This supersedes the old `datacenter_logs/<device>/<subsystem>/` rule
  — a single-repo-era convention that wrote to a cwd-relative root; reconciled to the
  canonical runtime location 2026-06-25, T-per-device-log-hierarchy.*
- **Log every state change and every interface crossing.** State changes: ticket status
  transitions, device lifecycle events (start/stop/restart/halt), routing decisions,
  auth/trust events. Interface crossings: channel post/read, DB write/read, subprocess
  spawn, MCP tool dispatch, device/shim boundary method calls. Log at INFO for crossings,
  DEBUG for high-frequency state changes (e.g. Hebbian edge weight updates).
  *Why: without a log at the crossing point, a bug at a device boundary is invisible — you
  can't tell whether the problem is in the sender or the receiver, or whether the message
  crossed at all. Enforced by audit check AR-009.*
- **Every ticket closes proven, or declares itself unproven — no discriminator.** A ticket that
  claims done closes only by pointing at a proof a hollow build couldn't pass (proof-on-close).
  The only honest alternative is to close `shipped-unproven` with a reason that **names the
  missing proof-lever** — visible, never posing as done. There is no "load-bearing enough to need
  proof" judgment call: that scoping was an escape hatch, and escape hatches are how hollow builds
  slip through (CP1). *(Supersedes the earlier "prove what's load-bearing" rule, 2026-06-21.)*
  *Why: a test that passes on hollow output is the signature of a missing spec — the cost of
  specifying intent doesn't vanish, it defers and leaks as hollow builds; the gate moves that cost
  up-front and visible. As tickets get conceptual, proving gets harder to define — that friction
  stays a deliberate nuisance (CP2 data, CP4 driver) until we find the lever that makes the class
  provable. `shipped-unproven` names the lever we still lack, so the nuisance is felt, not papered
  over — and the accumulated missing-levers are the backlog that earns the gates' eventual removal.*

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

## Session startup: /context-load

**Entry point after compaction.** When CC.0 (or any CC.x builder) resumes after `/autocompact`, the **first command is `/context-load`**. This loads the briefing post-compaction.

**Before running /context-load:**

1. **Compact cycle must have completed:** `/savestate` → `/autocompact` (done by prior session or manually).
2. **CC_TMUX_SESSION must be exported** — used by Step 0 to detect the CC instance (CC.0, CC.1, etc.) and restore the right availability flag from cachedstate. If not set, defaults to "CC.0".

**What /context-load does (10 steps):**

| Step | Purpose | Orients on |
|------|---------|-----------|
| 0 | Restore CC.x availability from cachedstate | "Am I available for Granny to route tickets?" |
| 0.25 | Check if prior day's slate closed cleanly | Soft prompt if prior day has open items |
| 0.5 | Set debug flag | Session observability |
| 1 | Load today's slate + summary | Current day's in-flight/planned/notes |
| 2a | Show recent decision titles + dates | Last 3 decisions (what was decided recently?) |
| 2b | List memory structure categories | (Memory store status) |
| 3 | Show active decisions with status | Last 5 decisions in what state? |
| 4 | Channel recent posts | What's been posted since last session? |
| 5 | Pending approvals queue | Which tickets await approval? |
| 5.5 | Stall check | Any tickets stuck >2 hours? |
| 5.6 | Unread inbox (urgency flagged) | Any high-urgency or Granny posts? |

**Availability edge case:**

If Step 0 finds no cached availability file, CC.x stays unavailable (no file = implicitly unavailable by design). CC.x will not receive tickets from Granny. To manually restore:
```bash
touch ~/.granny/available/CC.0.available.true
```

---

## Design workflow: /sorted → /migrate-decisions

**After filing decisions with `/sorted`**, decisions are written to `lab/design_docs/decisions/D-*.md` but not yet visible to `/context-load` or other tools that read the filesystem memory store. 

**Next step: run `/migrate-decisions`** to project the decision markdown into `devlab/runtime/memory/decisions/` as JSON:

```
/sorted <summary>                    # File decision + tickets
/migrate-decisions                   # Project decision to memory store
/context-load                        # Now sees the new decision
```

The projection is fail-open (migration errors never block `/sorted`). Run it immediately after `/sorted` completes so recent decisions appear in the next `/context-load`.

---

## Environment variables

One canonical env var. Everything else derives from it.

| Var | Default | Purpose |
|---|---|---|
| `UU_ROOT` | auto-detected via `unseen_university._uu_root.uu_root()` | Repo root. Set explicitly only when auto-detection fails. |

The runtime data dir is **derived, not an env var**: `unseen_university._uu_root.uu_home()` returns `~/.unseen_university` (logs, flags, device cachedstate, vault — not the repo, not the DB). Tests redirect it by monkeypatching `uu_home`, never by setting an env var. *(This supersedes the old `IGOR_HOME` env var — a single-repo-era holdover from before the bus/MCP/rack existed; eliminated in T-uu-eliminate-igor-home-env. Slates live in `devlab/runtime/memory/slates/` via `unseen_university.slate_store`; T-slate-location-canonical-devlab.)*

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
